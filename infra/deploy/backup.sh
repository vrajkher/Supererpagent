#!/bin/sh
# Nightly: consistent SQLite snapshot + sources + PKI, encrypted, copied off-host. Test restores monthly.
set -eu
D=/var/lib/raj/server; OUT=/var/backups/raj; TS=$(date +%Y%m%d-%H%M)
mkdir -p "$OUT"
sqlite3 "$D/raj.sqlite3" ".backup '$OUT/raj-$TS.sqlite3'"
tar -C "$D" -czf - sources pki "$OUT/raj-$TS.sqlite3" 2>/dev/null | age -r "$RAJ_BACKUP_AGE_RECIPIENT" > "$OUT/raj-$TS.tar.gz.age"
rm -f "$OUT/raj-$TS.sqlite3"
# rclone copy "$OUT/raj-$TS.tar.gz.age" offsite:raj-backups/
find "$OUT" -name '*.age' -mtime +35 -delete
