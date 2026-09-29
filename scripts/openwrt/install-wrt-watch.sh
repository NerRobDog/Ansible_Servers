#!/bin/sh
# Install wrt-watch for wrt-iv (tailnet name still wrt-mkch) on the Wyse Proxmox host.
# Run as root there, from the copied directory /usr/local/lib/wrt-watch. Does not enable the
# timer: that comes after the amalthea key is authorized and a --dry-run looks right.
#
#   sh /usr/local/lib/wrt-watch/install-wrt-watch.sh 'ssh-ed25519 AAAA...'   # router host key
#
# Expects /root/.ssh/wrt-mkch (router root key, copied from the Mac) to exist already.
set -e
HOSTKEY=${1:?pass the router host key line: ssh-ed25519 AAAA...}
LIB=/usr/local/lib/wrt-watch
[ -f /root/.ssh/wrt-mkch ] || { echo "missing /root/.ssh/wrt-mkch"; exit 1; }
chmod 600 /root/.ssh/wrt-mkch
chmod 755 "$LIB"/*.sh
mkdir -p /etc/wrt-watch /var/lib/wrt-watch

cat > /etc/wrt-watch/wrt-iv.env <<'EOF'
NAME=wrt-iv
TS_HOST=wrt-mkch
ZT_IP=172.23.234.116
JUMP=amalthea
KEY=/root/.ssh/wrt-mkch
TARGET_TS=1.102.4
EOF

# Pin the router host key for every name the watcher may use (same key on ZeroTier and tailnet).
grep -q '^wrt-mkch,' /root/.ssh/known_hosts 2>/dev/null ||
  echo "wrt-mkch,wrt-mkch.tailc06517.ts.net,100.125.40.117,172.23.234.116 $HOSTKEY" >> /root/.ssh/known_hosts

# Jump key for amalthea, usable only to open a tunnel to the router's ZeroTier SSH.
[ -f /root/.ssh/amalthea-jump ] || ssh-keygen -q -t ed25519 -N '' -C 'wyse wrt-watch jump' -f /root/.ssh/amalthea-jump
if ! grep -q '^Host amalthea$' /root/.ssh/config 2>/dev/null; then
  cat >> /root/.ssh/config <<'EOF'

Host amalthea
    HostName 194.87.187.210
    User ernestsh
    IdentityFile /root/.ssh/amalthea-jump
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
EOF
fi
echo "restrict,port-forwarding,permitopen=\"172.23.234.116:22\" $(cat /root/.ssh/amalthea-jump.pub)" > /root/.ssh/amalthea-jump.authorized_line

cat > /etc/systemd/system/wrt-watch@.service <<'EOF'
[Unit]
Description=wrt-watch: apply pending fixes to router %i when it is reachable
After=network-online.target tailscaled.service

[Service]
Type=oneshot
ExecStart=/bin/sh /usr/local/lib/wrt-watch/wrt-watch.sh /etc/wrt-watch/%i.env
TimeoutStartSec=15min
EOF
cat > /etc/systemd/system/wrt-watch@.timer <<'EOF'
[Unit]
Description=wrt-watch timer for router %i

[Timer]
OnBootSec=3min
OnUnitInactiveSec=2min

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
echo "installed; authorized_keys line for amalthea is in /root/.ssh/amalthea-jump.authorized_line"
