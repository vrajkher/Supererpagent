#!/bin/sh
# Run from a test client with a paired device key. Every check must print OK.
# Usage: verify-restrictions.sh <host> <key> <assigned_port>
set -u
H="$1"; K="$2"; P="$3"; O="-o BatchMode=yes -o ConnectTimeout=10 -i $K"
chk() { if [ "$1" = "$2" ]; then echo "OK   $3"; else echo "FAIL $3"; fi; }
ssh $O rajtunnel@"$H" true >/dev/null 2>&1; chk $? 1 "shell/command refused"
ssh $O -tt rajtunnel@"$H" >/dev/null 2>&1 </dev/null; [ $? -ne 0 ] && echo "OK   PTY refused" || echo "FAIL PTY"
sftp -b /dev/null $O rajtunnel@"$H" >/dev/null 2>&1; [ $? -ne 0 ] && echo "OK   SFTP refused" || echo "FAIL SFTP"
timeout 8 ssh $O -N -o ExitOnForwardFailure=yes -L 9999:127.0.0.1:22 rajtunnel@"$H" >/dev/null 2>&1; [ $? -ne 124 ] && echo "OK   local forward refused" || echo "FAIL local forward"
timeout 8 ssh $O -N -o ExitOnForwardFailure=yes -R 127.0.0.1:$((P+1)):127.0.0.1:1 rajtunnel@"$H" >/dev/null 2>&1; [ $? -ne 124 ] && echo "OK   other listen port refused" || echo "FAIL other port"
timeout 8 ssh $O -N -o ExitOnForwardFailure=yes -R 0.0.0.0:$P:127.0.0.1:1 rajtunnel@"$H" >/dev/null 2>&1; echo "CHECK on server: ss -ltn | grep $P must show 127.0.0.1 only"
timeout 8 ssh $O -N -o ExitOnForwardFailure=yes -R 127.0.0.1:$P:127.0.0.1:1 rajtunnel@"$H" >/dev/null 2>&1; [ $? -eq 124 ] && echo "OK   assigned port allowed" || echo "FAIL assigned port"
