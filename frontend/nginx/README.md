# Data Studio Nginx configuration

This configuration is intended for Nginx installed directly on the GPU host.
It listens publicly on port `8010` and proxies to Gunicorn on the private
address `127.0.0.1:8013`.

## Install

```bash
cd /data/dev/fa/python-version/data_platform
sudo cp frontend/nginx/data-platform.conf /etc/nginx/conf.d/data-platform.conf
sudo nginx -t
sudo systemctl reload nginx
```

Run the application backend separately:

```bash
cd /data/dev/fa/python-version/data_platform
source venv/bin/activate
gunicorn --bind 127.0.0.1:8013 --workers 1 --threads 4 --timeout 300 app:app
```

For detailed request and application events in the terminal, use:

```bash
gunicorn --bind 127.0.0.1:8013 --workers 1 --threads 4 --timeout 300 \
  --capture-output --access-logfile - --error-logfile - --log-level info app:app
```

Test on the GPU server:

```bash
curl http://10.0.30.100:8010/health
```

Then open `http://10.0.30.100:8010` from an authorized client network.

Do not copy this file into the existing prelogin Nginx container. It is a
separate server configuration and avoids changing the running prelogin app.
