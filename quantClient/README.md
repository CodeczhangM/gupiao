# 量化选股前端

这是一个无需打包的 Vue 静态前端，默认调用 Spring Boot 的 `/api/quant` 接口。

## 本地打开

直接用浏览器打开：

```text
/mnt/d/piao/quantClient/index.html
```

如果前端和 Spring 不在同一个域名或端口，可以在页面左侧修改接口地址，例如：

```text
http://127.0.0.1:8080/api/quant
```

## Nginx 示例

```nginx
location / {
    root /mnt/d/piao/quantClient;
    index index.html;
    try_files $uri $uri/ /index.html;
}

location /api/quant/ {
    proxy_pass http://127.0.0.1:8080/api/quant/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

