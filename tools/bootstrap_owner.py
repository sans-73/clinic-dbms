#!/usr/bin/env python3
"""Print SQL that creates the FIRST owner account (chicken-and-egg: create_staff needs an owner).
Usage:  python tools/bootstrap_owner.py owner 'MyStrongPassword' "Asha" "Verma" > /tmp/owner.sql
Then:   docker compose exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" clinic_db' < /tmp/owner.sql
Never commit the output (it contains a password hash).  Uses the SYS-SCHEDULER principal (staff_id 1) as the actor."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from security import hash_password

if len(sys.argv) < 3:
    sys.exit(__doc__)
login, pw = sys.argv[1], sys.argv[2]
given = sys.argv[3] if len(sys.argv) > 3 else 'Clinic'
family = sys.argv[4] if len(sys.argv) > 4 else 'Owner'
q = lambda s: "'" + s.replace("\\", "\\\\").replace("'", "''") + "'"
print(f"""USE clinic_db;
SET @app_user_id = 1, @app_role = 'SYSTEM';
CALL next_code('staff', @code);
INSERT INTO staff (display_code, principal_kind, given_name, family_name, login_subject) VALUES (@code, 'human', {q(given)}, {q(family)}, {q(login)});
SET @sid = LAST_INSERT_ID();
INSERT INTO staff_credential (staff_id, password_hash) VALUES (@sid, {q(hash_password(pw))});
INSERT INTO staff_role (staff_id, role_id, granted_by) SELECT @sid, role_id, 1 FROM `role` WHERE role_code = 'OWNER';
SELECT @sid AS owner_staff_id, @code AS owner_code;""")
