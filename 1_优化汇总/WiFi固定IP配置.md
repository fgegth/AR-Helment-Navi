# WiFi 固定 IP 配置 — DHCP 改静态

## 问题现象

板子每次重启 DHCP 随机分配 IP（.2 / .23 / .91 / .150 / .169 / .198 / .203 / .204 / .248 等），手机 App 需要手动改 IP 才能连接。

## 根因

板子默认用 udhcpc 动态获取 IP，路由器 DHCP 池每次可能分配不同地址。

## 解决方案

在 `/etc/init.d/S99hudnavi` 中，WiFi 连接成功后，杀掉 udhcpc 并手动设置固定 IP。

### 修改位置

`/etc/init.d/S99hudnavi` — WiFi 连接成功之后、启动 guard.sh 之前插入：

```sh
# === 固定IP ===
killall udhcpc 2>/dev/null
sleep 1
ifconfig wlan0 10.203.69.160 netmask 255.255.255.0 2>/dev/null
route add default gw 10.203.69.1 2>/dev/null
echo "[HUD] IP: $(ifconfig wlan0 2>/dev/null | grep 'inet ' | awk '{print $2}' | sed 's/addr://')"
```

### 效果

- 固定 IP：`10.203.69.160`
- 网关：`10.203.69.1`（路由器地址）
- 手机 App 设置一次 IP 永久有效

### 文件位置

```
2_BoardFiles/etc/init.d/S99hudnavi  （PC 端）
/etc/init.d/S99hudnavi               （板上部署路径）
```
