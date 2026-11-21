#!/usr/bin/env bash
# Ejecuta todas las pruebas de API (rápidas, sin navegador). Uso: bash tests/run_all.sh
set -e
cd "$(dirname "$0")"
for t in test_core test_fiscal test_v012 test_api13 test_api14 test_api15 test_api16; do
  echo "== $t"; python3 "$t.py" | tail -1
done
echo "TODAS LAS PRUEBAS DE API PASARON"
