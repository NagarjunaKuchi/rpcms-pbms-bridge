#!/bin/bash
. /home/claude/odoo17/venv/bin/activate
ADDONS=/home/claude/openg2p-program,/home/claude/openg2p/openg2p-registry,/home/claude/oca/queue,/home/claude/oca/openg2p-rest-framework,/home/claude/oca/odoo-formio,/home/claude/oca/openg2p-storage,/home/claude/odoo17/extra,/home/claude/work/rpcms-pbms-bridge
exec odoo --addons-path=$ADDONS --db_host=localhost --db_user=odoo --db_password=odoo --without-demo=all --stop-after-init --http-port=8099 "$@"
