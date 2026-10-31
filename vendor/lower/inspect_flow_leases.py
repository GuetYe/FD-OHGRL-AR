"""Read-only inspection. Never force-release leases or delete the database."""
import json
from flow_registry import Registry
if __name__=='__main__':
    rows=Registry().pending()
    print(json.dumps([{k:r.get(k) for k in ('flow_id','dscp','state','path','policy_version','client_closed_at','controller_error','switch_dpids','rules_absent_at')} for r in rows],ensure_ascii=False,indent=2))
