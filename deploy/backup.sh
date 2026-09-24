#!/bin/sh
# 定时备份 sidecar：在 compose 网络里用 pg_dump 落一份 custom 格式的库快照。
#
# ── 为什么是"容器里的循环"，而不是宿主 crontab ────────────────────────
# 1) pg_dump 的版本必须 **>= 服务端**。宿主装一个版本不匹配的 postgresql-client，
#    报错是 "server version mismatch"（aborting because of version mismatch），
#    而这台机器上往往没有 postgres 的软件源，装对版本本身就是一件麻烦事。
#    用与 db 服务**同一个** postgres:16 镜像，这个约束自动成立。
# 2) db 服务刻意只 `expose` 5432、不映射到宿主（见 docker-compose.yml）。在
#    compose 网络里直接连它，就不需要为了备份把数据库端口捅到公网。
# 3) `docker compose up -d` 一条命令就有备份，不依赖宿主装没装 cron、有没有配过。
#
# 代价是：这里是一个"间隔计时器"，不是墙钟调度（没有 cron 表达式）。
# 容器重启会立刻补一次备份，所以不会出现"重启后就再也没备份过"；
# 真要卡死在凌晨 3 点整跑，请在宿主用 cron 调用 `make backup`。
#
# ── 与 backend/scripts/backup_db.py 的关系 ──────────────────────────
# 那条路径（`make backup`）走宿主或 `docker exec`，适合手动/带外备份；
# 这条路径是常驻的自动备份。**两条路径的产物刻意完全一致**：
# custom 格式（-Fc）、文件名 `blog-<时间戳>.dump`、轮转只保留最近 N 份。
# 所以 README 里那条 `pg_restore` 恢复命令对两者都成立，文件也能互相接续。
# 轮转逻辑确实是第二份实现（postgres 镜像里没有 Python，没法复用那个脚本），
# 但它只有几行，且**判据是文件名**，不会与 Python 版产生语义分歧。
#
# ── 环境变量（都在 docker-compose.yml 里给了默认值）─────────────────
#   PGHOST/PGUSER/PGPASSWORD/PGDATABASE  连接信息（compose 注入）
#   BACKUP_DIR              容器内输出目录（默认 /backups，挂到宿主 ./backups）
#   BACKUP_KEEP             保留份数（默认 14，与 backup_db.py 一致）
#   BACKUP_INTERVAL_SECONDS 备份间隔（默认 86400 = 每天一次）
#   BACKUP_ONCE=1           只备份一次就退出（给验证脚本与一次性备份用）
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
KEEP="${BACKUP_KEEP:-14}"
INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
PGDATABASE="${PGDATABASE:-blog}"
# 失败后的重试间隔。一次网络抖动不该让"下一次备份"等满一整天：
# 备份的价值随时间衰减，而失败之后最需要的是尽快拿到一份成功的。
RETRY_SECONDS="${BACKUP_RETRY_SECONDS:-300}"

# 连接信息由 compose 注入；单独跑这个脚本时给出明确提示而不是 "unbound variable"
: "${PGUSER:?需要在环境里提供 PGUSER（连接用的数据库用户）}"
: "${PGPASSWORD:?需要在环境里提供 PGPASSWORD（连接用的口令）}"

# ── 目录可写性：先检查再干活 ────────────────────────────────────────
# 绑定的宿主目录在 Linux 上由 Docker 以 root 创建，而本容器**恰好**是 root
# （postgres 镜像的默认用户；它平时靠 entrypoint 把服务进程降权到 postgres，
# 我们覆写了 entrypoint，所以这里是 root）。这是刻意的：备份容器没有发布任何
# 端口、不接受外部输入，唯一的写权限就是那个备份目录；换成 uid 999 反而会让
# 权限问题在部署时以"备份静默失败"的形式出现 —— 那是备份脚本最坏的失败方式。
# 真遇到不可写（宿主目录被 chown 给别人），立刻大声退出，不要静默降级。
if ! mkdir -p "$BACKUP_DIR" 2>/dev/null || ! touch "$BACKUP_DIR/.write-test" 2>/dev/null; then
    echo "❌ 备份目录不可写：$BACKUP_DIR" >&2
    echo "   宿主机上执行（Linux）：sudo chown 0:0 ./backups  或  chmod 777 ./backups" >&2
    echo "   原因：绑定挂载的宿主目录属主不是本容器用户。" >&2
    exit 1
fi
rm -f "$BACKUP_DIR/.write-test"
# 清掉上一次被强杀（容器被 kill -9 / 机器断电）留下的半成品。
# 它不会参与轮转（后缀是 .dump.part，不匹配 blog-*.dump），
# 所以不主动清就永远不会被回收 —— 属于"慢慢占满磁盘"的那类垃圾。
rm -f "$BACKUP_DIR"/*.part 2>/dev/null || true

log() { echo "[backup] $(date '+%Y-%m-%d %H:%M:%S') $*"; }

# ── 轮转：只保留最新的 N 份 ─────────────────────────────────────────
# 没有轮转的备份等于没备份：磁盘迟早被塞满，而**满盘之后的那次备份会失败**，
# 那时你恰好没有可用备份。两种后缀一起算，理由同 backup_db.py。
prune() {
    # shellcheck disable=SC2012  # 只处理本目录自己的固定命名，ls 足够
    ls -1 "$BACKUP_DIR"/blog-*.dump "$BACKUP_DIR"/blog-*.db 2>/dev/null \
        | sort -Vr \
        | tail -n +"$((KEEP + 1))" \
        | while IFS= read -r stale; do
            rm -f "$stale"
            log "🧹 已删除旧备份：$(basename "$stale")（只保留最近 $KEEP 份）"
        done
}

# ── 一次备份 ────────────────────────────────────────────────────────
# 三步走：写临时文件 → 校验归档可读 → 改名。
# 关键在于**绝不产出一个看起来像备份的坏文件**：直接写最终文件名的话，
# pg_dump 中途失败会留下一个截断的 .dump，而恢复时才发现 —— 那时候
# 你正指望它救数据。校验用 pg_restore -l（读归档目录表），坏档立刻暴露。
backup_once() {
    stamp="$(date '+%Y%m%d-%H%M%S')"
    final="$BACKUP_DIR/blog-$stamp.dump"
    tmp="$final.part"
    rm -f "$tmp"

    # 结构还没建好就跳过：全新部署时这个 sidecar 可能比后端的迁移先醒过来，
    # 那时候 pg_dump 会成功产出一个"结构为空但格式合法"的小文件（实测 0.8 KB）。
    # 那是备份脚本最坏的输出 —— 它看起来像一份备份，恢复出来却什么都没有。
    # 没有 alembic_version 就等于"这个库还没被迁移过"，宁可这一轮不备份。
    if [ "$(psql -tAc "select count(*) from information_schema.tables where table_name = 'alembic_version'")" != "1" ]; then
        log "⚠️ 库还没有结构（alembic_version 不存在），本次跳过；等迁移完成后再备份"
        return 1
    fi

    if ! pg_dump -Fc -U "$PGUSER" -d "$PGDATABASE" -f "$tmp"; then
        rm -f "$tmp"
        log "❌ pg_dump 失败（库 $PGDATABASE@$PGHOST）。未生成备份文件。"
        return 1
    fi

    if ! pg_restore -l "$tmp" >/dev/null 2>&1; then
        rm -f "$tmp"
        log "❌ 备份归档校验失败（pg_restore -l 读不出目录表），已删除该文件。"
        return 1
    fi

    mv "$tmp" "$final"
    size="$(du -h "$final" | cut -f1)"
    log "✅ 已备份：$(basename "$final")（$size）"
    prune
    return 0
}

log "定时备份启动：每 ${INTERVAL}s 一次，输出 $BACKUP_DIR，保留最近 $KEEP 份"
log "恢复方式：docker compose exec -T db pg_restore --clean --if-exists -U $PGUSER -d $PGDATABASE < <备份文件>"

if [ "${BACKUP_ONCE:-0}" = "1" ]; then
    backup_once
    exit $?
fi

# 循环而不是 cron：容器是长驻的，重启由 compose 负责（restart: unless-stopped）。
# 失败**不退出**：一次网络抖动不该让备份从此停摆（PID 1 退出会让容器重启，
# 那看起来像"一直在重启"，反而不如"一直活着、日志里写着失败"清楚）。
# 但失败要留下响亮的日志，并改用更短的重试间隔。
while true; do
    if backup_once; then
        sleep "$INTERVAL"
    else
        log "⚠️ 本次备份失败，${RETRY_SECONDS}s 后重试（排查入口：docker compose logs backup）"
        sleep "$RETRY_SECONDS"
    fi
done
