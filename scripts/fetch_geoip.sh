#!/usr/bin/env sh
# Download the DB-IP City Lite GeoIP database (free, CC BY 4.0, no account required).
#
# This is the ONLY step in btctrace that touches the network, and it is optional: without
# it, ingestion falls back to the country and ASN carried in the dataset itself. Run it
# once on a connected machine and copy data/geoip/ across to an isolated one.
#
# Attribution is a licence condition -- the dashboard footer credits DB-IP. Keep it.
set -eu

DEST_DIR="${1:-data/geoip}"
DEST="$DEST_DIR/dbip-city-lite.mmdb"
MONTH="$(date -u +%Y-%m)"
URL="https://download.db-ip.com/free/dbip-city-lite-$MONTH.mmdb.gz"

mkdir -p "$DEST_DIR"
if [ -f "$DEST" ]; then
  echo "already present: $DEST"
  exit 0
fi

echo "downloading $URL (~120 MB)"
if ! curl -fSL "$URL" -o "$DEST.gz"; then
  # DB-IP publishes monthly and removes the newest file briefly around the rollover.
  MONTH="$(date -u -d '1 month ago' +%Y-%m 2>/dev/null || date -u -v-1m +%Y-%m)"
  URL="https://download.db-ip.com/free/dbip-city-lite-$MONTH.mmdb.gz"
  echo "current month unavailable, falling back to $URL"
  curl -fSL "$URL" -o "$DEST.gz"
fi

gunzip -f "$DEST.gz"
echo "installed $DEST"
echo "IP geolocation by DB-IP (https://db-ip.com) -- CC BY 4.0"
