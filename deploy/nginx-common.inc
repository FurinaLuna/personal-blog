# 与 TLS 无关的公共部分：upstream。
#
# 为什么单独一个文件：`upstream` 只能出现在 **http 上下文**，不能写进 server 块，
# 而 nginx.conf（纯 HTTP）与 nginx.https.conf（自管证书）都要用它。
# 复制两份的话，将来改后端地址一定会漏改其中一份 —— 所以由两边 include。
#
# 注意文件名是 `.inc` 而不是 `.conf`：官方 nginx 镜像的主配置里有
# `include /etc/nginx/conf.d/*.conf;`，叫 .conf 会被自动加载成"第二个 server"，
# 于是 upstream 会被定义两遍、站点被发布两次。

upstream blog_backend {
    # Docker Compose 的服务名；裸机部署改成 127.0.0.1:8000
    server backend:8000;
    keepalive 32;
}
