#!/usr/bin/env python3
"""Builds docs/pooling/POOLING_GUIDE.md and docs/pooling/TRACK_B_POOLING_GUIDE.pdf from ONE content list (they can never differ)."""
import os
from xml.sax.saxutils import escape
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

C = []
def h1(t): C.append(('h1', t))
def h2(t): C.append(('h2', t))
def p(t): C.append(('p', t))
def bl(*items): C.append(('ul', list(items)))
def code(t): C.append(('code', t.strip('\n')))
def tbl(widths, *rows): C.append(('tbl', widths, [list(r) for r in rows]))

C.append(('title', 'Clinic DBMS: Track B Integration Guide', 'DB leader ke liye: changes, naye phases aur fixes ko jaldi se system mein kaise jodna hai'))

h1('1. Abhi system kahan tak pahuncha hai')
p('Phase 0 se Phase 3 tak ka database kaam (P2 schema, P3-1 se P3-7 logic aur security, aur P3-8 tests) likha ja chuka hai. '
  'Poora schema aapke ER model (docs/design/01_er_model.md, rev 2) ke 103 entities se banta hai (+1 documented extension table). '
  'IMPORTANT: yeh sab abhi real MySQL par chala nahi hai, kyunki jahan bana wahan MySQL nahi tha. Static verifier pass hai, par pehli run mein 5-15 chhoti SQL errors expected hain.')
tbl([4.6, 3.0, 4.6],
    ['Kya', 'Status', 'Kahan'],
    ['Schema: 104 tables, 13 late FKs', 'Generated, run baaki', 'db/schema/*.sql (tools/schema_spec.py se)'],
    ['P3-1 Foundation (352 triggers, approvals, alerts, login)', 'Likha', 'db/logic/01_foundation_triggers.sql'],
    ['P3-2 Patients, scheduling, visits, stay, vitals', 'Likha', 'db/logic/02_patients_visits.sql'],
    ['P3-3 Allergy, prescriptions (versions), orders, outside labs', 'Likha', 'db/logic/03_prescriptions_labs.sql'],
    ['P3-4 Stock, dispensing, counter sale, custody, doses, leftovers', 'Likha', 'db/logic/04_pharmacy.sql'],
    ['P3-5 Billing, payments, refunds, discharge, dues', 'Likha', 'db/logic/05_billing_discharge.sql'],
    ['P3-6 Alerts, owner summary, exception reports, family links', 'Likha', 'db/logic/06_owner_alerts_links.sql'],
    ['P3-7 Roles, accounts, grants (181 procedure grants, 58 views)', 'Generated', 'db/security/01_roles_and_grants.sql'],
    ['P3-8 Rule tests: 127 tests + forbidden-action script', 'Likha, run baaki', 'db/tests/'],
    ['Phase 4, 5, 6, 7', 'Aage', 'INTEGRATION_CONTRACT.md follow karo'])

h1('2. Quick start (pehli baar)')
code(r"""python tools/verify_kit.py            # 'all checks passed'
cp .env.example .env                  # password set karo
docker compose down -v && docker compose up -d
bash tools/run_sql_checked.sh         # pehli error par ruk jaata hai
python tools/bootstrap_owner.py owner 'StrongPass' Asha Verma > owner.sql
docker compose exec -T mysql sh -c \
 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" clinic_db' < owner.sql
docker compose exec -T mysql sh -c \
 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" clinic_db' < db/tests/rule_tests.sql
bash db/tests/forbidden_actions.sh    # har line PASS honi chahiye""")
p('Rule tests ka output: har test ka PASS/FAIL aur rule number. FAIL aaye to us rule wali file fix hogi (section 3 ka impact map madad karega). '
  'Gate 2 tab, jab run_sql_checked.sh zero errors de. Gate 3 tab, jab saare 127 tests PASS hon.')

h1('3. Agar P1-3 / P1-4 (ya review) se design change aaye')
p('Golden rule: schema ka single source of truth tools/schema_spec.py hai. Generated files (db/schema/*, db/logic/01_*) kabhi hand se edit nahi hoti.')
h2('Step by step')
bl('python tools/compare_design.py <unka 02_table_design.md>  ->  dikhata hai kaun si tables sirf unke doc mein hain aur kaun si sirf hamare spec mein.',
   'Har farak par faisla: (a) unka sahi hai -> spec badlo. (b) hamara sahi hai -> CR likho. Dono ko alag mat chalne do.',
   'python tools/impact_map.py <table>  ->  us table ko chhune wale saare procedures, views, triggers aur tests ki list. Wahi aapka to-do hai.',
   'Spec badlo, phir: python tools/build_db.py, python tools/verify_kit.py, bash tools/run_sql_checked.sh, rule tests.',
   'Agar naya table/column kisi procedure ka hissa banna hai: procedure edit karo (db/logic/0X), @api comment update karo, python tools/gen_db_api_docs.py aur python tools/gen_security.py chalao.',
   'Naya rule test: tools/gen_rule_tests.py mein test() line jodo, regenerate, run.')
h2('Change ka type -> kya karna hai')
tbl([3.6, 4.4, 4.2],
    ['Change', 'Kahan badlo', 'Kya re-run karo'],
    ['Naya column (nullable)', 'schema_spec.py ke us table ki cols list', 'build_db, verify, run_sql_checked'],
    ['Naya NOT NULL column', 'schema_spec + har INSERT jo us table mein hota hai (impact_map dikhayega)', 'build_db, verify, tests'],
    ['Column rename', 'schema_spec + impact_map ki saari files (search/replace) + tests', 'verify_kit column checker pakdega'],
    ['Naya table', 'schema_spec (table + cluster + rules) ; phir procedure/view', 'build_db, gen_security, gen_db_api_docs'],
    ['Table ko tod ke normalize karna (P1-4 decomposition)', 'schema_spec mein naye tables; purani table ki jagah views banao jo purane column naam dete hain', 'impact_map se saare callers; tests'],
    ['Enum/status naya value', 'schema_spec ka E(...) + related procedure ke checks', 'build_db, tests'],
    ['CHECK / unique rule badla', 'schema_spec checks/uniques', 'build_db, tests'],
    ['Naya business rule (R-number)', 'trigger snippet (bi/bu) ya procedure + test', 'verify, tests'],
    ['Role/permission badli', 'procedure ke @api roles=... ; view access: tools/gen_security.py', 'gen_security, forbidden_actions.sh'],
    ['Policy value (limit, days)', 'clinic_settings seed (schema_spec SEEDS) ya sp_set_setting', 'tests'])
p('P1-4 (normalization) se aksar yeh nikalta hai: koi table BCNF/4NF mein nahi hai. Aise me decomposition ko spec mein dalo, '
  'aur purani table ke naam par ek view rakho taaki baaki code na toote. Ek hi baar mein ek change karo aur har baar verify_kit chalao.')

h1('4. Agar Phase 4 / 5 / 6 / 7 ka kaam koi aur karke de')
p('Jis AI se bhi yeh kaam karwao, uske prompt mein pehle docs/pooling/INTEGRATION_CONTRACT.md paste karo. Usme saare niyam hain: '
  'kaun se tables aur procedures use karne hain, connection kaise set karni hai, aur kya deliver karna hai.')
tbl([2.2, 5.4, 4.6],
    ['Phase', 'Integration ke liye check', 'Common galti'],
    ['P4-1 seed', 'Sirf procedures se data; staff create_staff se; price items CONSULT aur STAY_DAYCARE sp_set_price se', 'Seed mein seedha INSERT (triggers rok denge)'],
    ['P4-2 bulk', 'Alag database clinic_bench, sirf schema files, triggers ke bina; recorded_by aur role khud bharo', 'Schema aur logic dono load karna (bahut slow)'],
    ['P4-3 benchmarks', 'Index sirf benchmark ke liye alag script mein; schema_spec tab badlo jab index permanent ho', 'Production schema ko chupke se badalna'],
    ['P4-4 showcase', 'Read-only SQL, tables/views baseline se', 'Naye column naam invent karna'],
    ['P5 web app', 'app/routes/<naam>.py; DB calls call(conn, "sp_x", ...) helper se; roles clinic_<role>; @app_user_id/@app_role har request', 'Python mein rule dobara likhna'],
    ['P6 demos', 'Procedure aur rule numbers docs/db_api aur rule_tests se', 'Purane naam use karna'])
h2('Integration checklist (kaam wapas aane par)')
bl('Files ko sahi folders mein rakho (har task sirf apni files likhta hai).',
   'python tools/verify_kit.py (web app ke CALL names, argument counts aur views bhi check hote hain).',
   'bash tools/run_sql_checked.sh phir rule tests: kuch toota to turant pata chalega.',
   'Agar AI ko kuch API mein nahi mila aur usne workaround kiya: use CR likhwao, workaround reject karo.',
   'Commit message mein task ID. Phir mujhe files + HANDOFF NOTE bhejo; main conflicts pool karunga.')

h1('5. Sabse kaam ke tools (ek line mein)')
tbl([4.2, 7.6],
    ['Command', 'Kaam'],
    ['python tools/build_db.py', 'schema_spec se schema, late FKs, triggers aur baseline design doc banata hai'],
    ['python tools/verify_kit.py', 'ER match, FK, columns (tables+views), CALL arguments, templates, Python syntax'],
    ['python tools/impact_map.py [table]', 'Kis table ko kaun chhuta hai (change ka to-do)'],
    ['python tools/compare_design.py <file>', 'Dusre design doc ko hamare spec se compare'],
    ['python tools/gen_db_api_docs.py', 'docs/db_api/*.md ko SQL ke @api comments se banata hai'],
    ['python tools/gen_security.py', 'roles, accounts, grants, security matrix, forbidden-action script'],
    ['python tools/gen_rule_tests.py', 'db/tests/rule_tests.sql (127 tests)'],
    ['bash tools/run_sql_checked.sh', 'Sab SQL files load; pehli error par ruk ke file batata hai'],
    ['python tools/bootstrap_owner.py', 'Pehla owner account'],
    ['python tools/make_guide.py', 'Yeh guide (md + pdf) dobara banata hai'])

h1('6. Pehli run mein expected errors')
tbl([4.4, 7.4],
    ['Error', 'Matlab / fix'],
    ['1418 DETERMINISTIC / NO SQL / READS SQL DATA', 'compose flag --log-bin-trust-function-creators=1 nahi laga: docker compose down -v phir up'],
    ['3780 / 1215 FK type mismatch', 'schema_spec mein dono columns ka type same karo'],
    ['1064 syntax error near ...', 'Line number aur file mujhe do. Generated ho to tools/ mein fix'],
    ['1442 cannot update table in trigger', 'Trigger usi table ko modify kar raha hai; mujhe batao'],
    ['1305 PROCEDURE/FUNCTION does not exist', 'Load order: run_all.sql ki order ya naam typo; verify_kit ye pehle pakadta hai'],
    ['Test FAIL: no error was raised', 'Wo rule enforce nahi ho raha; rule number se file dhundo'],
    ['Test FAIL: Duplicate / Not enough stock', 'Pehle ke run ka data; db/reset.sql + run_all dobara'])

h1('7. Safety')
bl('Real patient data, .env ya password kisi AI mein paste mat karo. DEV passwords (Dev#Owner2026 jaise) sirf local demo ke liye hain: ALTER USER se badlo.',
   'owner.sql commit mat karo. Koi AI bole "tests pass", aap khud chalao.',
   'Gate tags: design-v1 (aapka Phase 1), schema-v1 (run_sql_checked zero errors), logic-v1 (127 tests PASS).')

h1('Appendix: design decisions (details: docs/pooling/CR_LOG_TRACK_B.md)')
bl('CR-2: merge event rows ko rewrite nahi karta; patient_merge_log redirect hai (canonical_patient()).',
   'CR-B1: staff_credential extension (password hash). CR-B2: message_outbox.rendered_text extension.',
   'CR-B4: password hashing scrypt (stdlib). CR-B10: ek visit ka ek bill; counter sale ka alag payment/refund path.',
   'CR-B12: galat patient ki report store nahi hoti; message wapas aata hai aur interrupt alert bachta hai.',
   'CR-B14: emergency override sirf paise wala step chhodta hai; sign-off, doses, reports phir bhi chahiye.')


# ----------------------------------------------------------------- markdown
def to_md():
    out = []
    for it in C:
        k = it[0]
        if k == 'title': out += [f'# {it[1]}', f'_{it[2]}_', '']
        elif k == 'h1': out += [f'## {it[1]}', '']
        elif k == 'h2': out += [f'### {it[1]}', '']
        elif k == 'p': out += [it[1], '']
        elif k == 'ul': out += [f'- {x}' for x in it[1]] + ['']
        elif k == 'code': out += ['```', it[1], '```', '']
        elif k == 'tbl':
            r = it[2]
            out += ['| ' + ' | '.join(r[0]) + ' |', '|' + '---|' * len(r[0])] + ['| ' + ' | '.join(x) + ' |' for x in r[1:]] + ['']
    return '\n'.join(out)


# ----------------------------------------------------------------- pdf
def to_pdf(path):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Preformatted, ListFlowable, ListItem, PageBreak
    ss = getSampleStyleSheet()
    body = ParagraphStyle('b', parent=ss['BodyText'], fontSize=9.5, leading=13.5, spaceAfter=5)
    h1s = ParagraphStyle('h1', parent=ss['Heading1'], fontSize=15, spaceBefore=12, spaceAfter=6, textColor=colors.HexColor('#1F3A5F'))
    h2s = ParagraphStyle('h2', parent=ss['Heading2'], fontSize=11.5, spaceBefore=8, spaceAfter=4, textColor=colors.HexColor('#2E5E8C'))
    cs = ParagraphStyle('c', parent=ss['Code'], fontSize=7.4, leading=10.5, backColor=colors.HexColor('#F2F4F7'), borderPadding=4, spaceAfter=8)
    cell = ParagraphStyle('cell', parent=body, fontSize=8.3, leading=10.5, spaceAfter=0)
    cellh = ParagraphStyle('cellh', parent=cell, textColor=colors.white, fontName='Helvetica-Bold')
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.6 * cm, bottomMargin=1.6 * cm,
                            title='Track B Kit - Pooling Guide', author='Track B')
    W = A4[0] - 3.6 * cm
    fl = []
    for it in C:
        k = it[0]
        if k == 'title':
            fl += [Spacer(1, 1.2 * cm), Paragraph(escape(it[1]), ParagraphStyle('t', parent=ss['Title'], fontSize=24, textColor=colors.HexColor('#1F3A5F'))),
                   Paragraph(escape(it[2]), ParagraphStyle('s', parent=body, fontSize=11, alignment=1, textColor=colors.grey)), Spacer(1, 0.6 * cm)]
        elif k == 'h1': fl.append(Paragraph(escape(it[1]), h1s))
        elif k == 'h2': fl.append(Paragraph(escape(it[1]), h2s))
        elif k == 'p': fl.append(Paragraph(escape(it[1]), body))
        elif k == 'ul':
            fl.append(ListFlowable([ListItem(Paragraph(escape(x), body), leftIndent=12) for x in it[1]], bulletType='bullet', bulletFontSize=7, leftIndent=14))
            fl.append(Spacer(1, 4))
        elif k == 'code': fl.append(Preformatted(it[1], cs))
        elif k == 'tbl':
            ws = [w / sum(it[1]) * W for w in it[1]]
            rows = [[Paragraph(escape(c), cellh if i == 0 else cell) for c in r] for i, r in enumerate(it[2])]
            t = Table(rows, colWidths=ws, repeatRows=1)
            t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F3A5F')), ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#B8C2CC')),
                                   ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F6F8FA')]),
                                   ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4)]))
            fl += [t, Spacer(1, 8)]
    def footer(c, d):
        c.saveState(); c.setFont('Helvetica', 8); c.setFillColor(colors.grey)
        c.drawString(1.8 * cm, 1.0 * cm, 'Track B pooling guide'); c.drawRightString(A4[0] - 1.8 * cm, 1.0 * cm, f'page {d.page}'); c.restoreState()
    doc.build(fl, onFirstPage=footer, onLaterPages=footer)


if __name__ == '__main__':
    os.makedirs(os.path.join(ROOT, 'docs/pooling'), exist_ok=True)
    open(os.path.join(ROOT, 'docs/pooling/POOLING_GUIDE.md'), 'w', encoding='utf-8').write(to_md())
    to_pdf(os.path.join(ROOT, 'docs/pooling/TRACK_B_POOLING_GUIDE.pdf'))
    print('guide written')
