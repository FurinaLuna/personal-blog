#!/bin/sh
# 后端容器入口：先把数据库结构升到最新，再启动服务。
#
# 为什么由容器自己跑迁移：
#   compose 里 DB_AUTO_CREATE 默认 false（生产不能靠 create_all 改表），
#   于是「忘了跑迁移」的后果是应用起不来 —— 而文档原本让人在 `up -d` 之后
#   再 `exec` 进去补一条迁移，可这时候容器正在重启循环里，exec 根本进不去。
#   让入口先迁移，`docker compose up -d --build` 才真的是一条命令可用。
#
# ⚠️ 前提是**单副本**（compose 里 backend 就是一份）。多副本同时启动会互相
#    争抢 alembic_version 的写入，那时请把 RUN_MIGRATIONS_ON_STARTUP 设为
#    false，改由发布流程单独执行一次 `alembic upgrade head`。
#
# ⚠️ 本文件必须是 LF 换行。带 CRLF 的 .sh 在容器里会以
#    `exec format error` 或「no such file or directory」失败 —— 报错长得像
#    「文件不存在」，其实只是行尾多了个 \r，很容易查错方向。
#    .gitattributes 已为 *.sh 钉死 eol=lf，Dockerfile 里还有一道兜底替换。
set -e

if [ "${RUN_MIGRATIONS_ON_STARTUP:-true}" != "false" ]; then
    echo "[entrypoint] 迁移数据库结构：alembic upgrade head"
    alembic upgrade head
    echo "[entrypoint] 迁移完成"
else
    echo "[entrypoint] RUN_MIGRATIONS_ON_STARTUP=false，跳过迁移"
fi

# exec：让 uvicorn 成为 PID 1，docker stop 的 SIGTERM 才能直达它，
# 否则信号停在 shell 上，容器每次都要等超时才被杀掉。
exec "$@"
