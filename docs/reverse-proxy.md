# Behind a reverse proxy

A reverse proxy is the way to reach an instance from outside your own network, and the
tidiest way to get a certificate browsers accept without a warning. The container then
runs with `TLS_MODE=off` and the proxy does the encrypting.

**This is the only mode in which the instance should be reachable from the internet.**
`TLS_MODE=off` on its own means plain HTTP — it is safe behind a proxy on the same
machine and nowhere else.

*[Back to the README](../README.md)*

## Caddy

Caddy fetches and renews a Let's Encrypt certificate by itself, which makes it the
shortest correct answer. The example below is the whole configuration.

```
# Caddyfile
kehrwoche.example.org {
	reverse_proxy kehrwoche:8080
}
```

```yaml
# docker-compose.yml
services:
  kehrwoche:
    image: ghcr.io/chrisswalters/kehrwoche:1
    restart: unless-stopped
    environment:
      TLS_MODE: "off"
      FORWARDED_ALLOW_IPS: "*"
    volumes:
      - kehrwoche-data:/data
    # No ports: — only the proxy talks to it.

  caddy:
    image: caddy:2
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
    depends_on:
      - kehrwoche

volumes:
  kehrwoche-data:
  caddy-data:
  caddy-config:
```

Three things are doing quiet work here:

* **The application container publishes no ports.** It is reachable only inside the
  compose network, under its service name. Nothing can bypass the proxy.
* **Caddy sends `X-Forwarded-For` on its own**, which is what the rate limits need to tell
  visitors apart.
* **`FORWARDED_ALLOW_IPS` decides whether that header is believed** — and it is the line
  people leave out. By default the server only trusts forwarding headers from `127.0.0.1`,
  and a proxy in a neighbouring container is not that. Without this line every request
  appears to come from the proxy, all visitors share one rate-limit bucket, and one person
  mistyping their password locks out the whole household. `*` is safe **only** in exactly
  this shape, where nothing but the proxy can reach the container; if the application port
  is published, name the proxy's address instead.

Uploads need no special setting: the application caps them at 10 MB itself, well under
Caddy's defaults.

This example was run as it stands — Caddy in front, the application with `TLS_MODE=off`
and no published port — and checked both ways round: without `FORWARDED_ALLOW_IPS` the
application sees the proxy as the client, with it the real address arrives.

## nginx

```nginx
server {
    listen 443 ssl;
    server_name kehrwoche.example.org;

    ssl_certificate     /etc/letsencrypt/live/kehrwoche.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/kehrwoche.example.org/privkey.pem;

    # Pictures are capped at 10 MB by the application; nginx defaults to 1 MB.
    client_max_body_size 12m;

    location / {
        proxy_pass http://kehrwoche:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

The four headers are not optional. Without `X-Forwarded-For` the rate limiting counts
every visitor as the same one; without `X-Forwarded-Proto` the application cannot tell it
is being served over HTTPS.

## Traefik

Labels on the application service, with a router and certificate resolver you have already
configured:

```yaml
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.kehrwoche.rule=Host(`kehrwoche.example.org`)"
      - "traefik.http.routers.kehrwoche.tls.certresolver=letsencrypt"
      - "traefik.http.services.kehrwoche.loadbalancer.server.port=8080"
```

Traefik sets the forwarding headers by default.

## What a proxy has to do

Whatever you use, five things have to be true:

1. **Pass the client address** as `X-Forwarded-For`, or the rate limits are useless.
2. **Let the application believe it** by setting `FORWARDED_ALLOW_IPS` on the container.
   Sending the header and not trusting it has exactly the same effect as not sending it.
3. **Pass the protocol** as `X-Forwarded-Proto`.
4. **Allow 10 MB request bodies**, or profile pictures from a modern phone will fail.
5. **Do not cache `/api/`.** The application sets its own caching headers; pictures are
   named after their content and may be cached forever, everything else may not.

Do not add HSTS at the proxy while you are still testing with a self-signed certificate on
another port — the browser will remember the policy for the whole host name and refuse to
let you past the warning afterwards.

## Why HTTPS is worth the trouble

Beyond the obvious: browsers reserve a number of features for what they call a *secure
context*, and plain HTTP over a LAN address is not one — no matter that the network is
yours.

* **Copying the join code to the clipboard** uses a browser interface that only exists
  over HTTPS. Kehrwoche falls back to selecting the text so you can copy it by hand, but
  the one-tap version needs a secure context.
* **Adding the app to the home screen** installs it as a proper app — its own icon, its
  own window, no address bar — only over HTTPS. Over HTTP you get a bookmark that opens in
  a browser tab.

A self-signed certificate is enough for both: once the warning is confirmed on a device,
that device treats the origin as secure. That is why `self-signed` is the default rather
than `off`.
