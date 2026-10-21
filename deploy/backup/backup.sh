#!/bin/sh
# Sauvegarde PostgreSQL (format custom pg_dump, compressé), rotation sur BACKUP_RETENTION_DAYS jours.
#   backup.sh loop  : exécution quotidienne à BACKUP_TIME (heure locale, TZ du conteneur)
#   backup.sh now   : sauvegarde immédiate
set -eu

DIR=/backups
RETENTION="${BACKUP_RETENTION_DAYS:-14}"
AT="${BACKUP_TIME:-02:30}"

run_backup() {
  stamp=$(date +%Y%m%d_%H%M%S)
  tmp="$DIR/.sms_${stamp}.dump.partial"
  out="$DIR/sms_${stamp}.dump"
  echo "[$(date '+%F %T')] sauvegarde -> $out"
  if pg_dump --format=custom --compress=9 --no-owner --file="$tmp"; then
    mv "$tmp" "$out"
    size=$(du -h "$out" | cut -f1)
    echo "[$(date '+%F %T')] OK ($size)"
  else
    rm -f "$tmp"
    echo "[$(date '+%F %T')] ÉCHEC de pg_dump" >&2
    return 1
  fi
  # Rotation : suppression des sauvegardes plus anciennes que la rétention
  find "$DIR" -maxdepth 1 -name 'sms_*.dump' -mtime +"$RETENTION" -print -delete
}

seconds_until() {
  target=$(date -d "$(date +%F) $1" +%s 2>/dev/null || date -D '%Y-%m-%d %H:%M' -d "$(date +%F) $1" +%s)
  now=$(date +%s)
  [ "$target" -le "$now" ] && target=$((target + 86400))
  echo $((target - now))
}

case "${1:-loop}" in
  now)
    run_backup
    ;;
  loop)
    mkdir -p "$DIR"
    echo "Sauvegardes quotidiennes à $AT (rétention $RETENTION jours) dans $DIR"
    while true; do
      wait_s=$(seconds_until "$AT")
      echo "Prochaine sauvegarde dans $((wait_s / 3600)) h $(((wait_s % 3600) / 60)) min"
      sleep "$wait_s"
      run_backup || true
      sleep 60
    done
    ;;
  *)
    echo "usage : backup.sh [loop|now]" >&2
    exit 2
    ;;
esac
