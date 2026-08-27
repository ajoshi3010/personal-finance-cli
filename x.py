#!/usr/bin/env python3
"""Personal cash-flow mandate and bucket-debt tracker."""
import argparse
import getpass
import os
import readline
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

# Resolve symlinks so a launcher installed in ~/.local/bin still uses the
# database shipped beside this source file.
DB_FILE = Path(os.environ.get("FINANCE_DB_FILE", Path(__file__).resolve().with_name("finance.db")))


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def paise(value):
    return int(Decimal(str(value)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP) * 100)


def fmt(value):
    return f"₹{Decimal(int(value)) / 100:,.2f}"


def pid(n): return f"P{n:03d}"
def mid(n): return f"M{n:03d}"
def did(n): return f"D{n:03d}"


def prompt(label, options=None):
    """Read a line, optionally offering dynamic readline tab completion.

    ``options`` may be a callable (evaluated when completion starts) or an
    iterable. Completion is deliberately disabled for hidden password input.
    """
    if options is None:
        readline.set_completer(None)
        return input(label)
    provider = options if callable(options) else lambda: options

    def completer(text, state):
        if state == 0:
            values = [str(value) for value in provider()]
            # Complete the current whitespace-delimited token, allowing
            # multiple payment IDs such as "P001 P003".
            line = readline.get_line_buffer()
            token = line.rsplit(None, 1)[-1] if line and not line.endswith((" ", "\t")) else ""
            prefix = token or text
            completer.matches = [value for value in values if value.lower().startswith(prefix.lower())]
        try:
            return completer.matches[state]
        except IndexError:
            return None

    completer.matches = []
    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")
    try:
        return input(label)
    finally:
        readline.set_completer(None)


def all_commands():
    return ['salary_credited', 'outstandings', 'add_bucket', 'show_buckets',
            'add_mandate', 'add_debt', 'remove_mandate', 'show_mandates',
            'remove_debt', 'show_payments', 'remove_buckets', 'remove_bucket',
            'show_debts', 'pay_outstanding']


def interactive_cli():
    """Run a small command loop so the application can be used as a shell."""
    handlers = {
        'salary_credited': salary_credited, 'outstandings': outstandings,
        'show_buckets': show_buckets, 'add_mandate': add_mandate,
        'add_debt': add_debt, 'remove_mandate': remove_mandate,
        'show_mandates': show_mandates, 'remove_debt': remove_debt,
        'show_payments': show_payments, 'remove_buckets': remove_buckets,
        'remove_bucket': remove_bucket, 'show_debts': show_debts,
        'pay_outstanding': pay_outstanding,
    }
    commands = all_commands() + ['help', 'exit', 'quit']
    print("Personal Finance CLI interactive mode. Type 'help' for commands; 'exit' to leave.")
    while True:
        try:
            command = prompt("finance> ", commands).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if command in ('exit', 'quit'):
            break
        if command == 'help':
            print("Available commands: " + ', '.join(all_commands()))
            print("Use 'exit' or 'quit' to leave interactive mode.")
            continue
        if not command:
            continue
        handler = handlers.get(command)
        if handler is None:
            print(f"Unknown command: {command}. Type 'help' for available commands.")
            continue
        try:
            handler()
        except (EOFError, KeyboardInterrupt):
            print("\nCommand cancelled.")


def database_options(query):
    c = db()
    try:
        return [row[0] for row in c.execute(query)]
    finally:
        c.close()


def active_bucket_names():
    return database_options("SELECT name FROM buckets WHERE active=1 ORDER BY name")


def destination_names():
    return database_options("SELECT DISTINCT destination FROM mandates ORDER BY destination")


def active_mandate_ids():
    return [mid(value) for value in database_options("SELECT id FROM mandates WHERE active=1 ORDER BY id")]


def active_debt_ids():
    return [did(value) for value in database_options("SELECT id FROM debts WHERE active=1 ORDER BY id")]


def outstanding_payment_ids():
    return [pid(value) for value in database_options("SELECT id FROM payments WHERE status='OUTSTANDING' ORDER BY id")]


def yes_no_options():
    return ['y', 'n']


def initialize_db():
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS buckets (id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS mandates (id INTEGER PRIMARY KEY AUTOINCREMENT,
      source TEXT NOT NULL, destination TEXT NOT NULL, amount REAL NOT NULL,
      active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(source) REFERENCES buckets(name))""")
    c.execute("""CREATE TABLE IF NOT EXISTS debts (id INTEGER PRIMARY KEY AUTOINCREMENT,
      borrower TEXT NOT NULL, lender TEXT NOT NULL, original_amount REAL NOT NULL,
      outstanding_amount REAL NOT NULL, repayment_months INTEGER NOT NULL,
      monthly_repayment REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(borrower) REFERENCES buckets(name), FOREIGN KEY(lender) REFERENCES buckets(name))""")
    c.execute("""CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY AUTOINCREMENT,
      source TEXT NOT NULL, destination TEXT NOT NULL, amount REAL NOT NULL,
      amount_paise INTEGER NOT NULL, payment_type TEXT NOT NULL CHECK(payment_type IN ('MANDATE','DEBT')),
      mandate_id INTEGER, debt_id INTEGER, salary_run TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'OUTSTANDING' CHECK(status IN ('OUTSTANDING','PAID')),
      paid_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(mandate_id) REFERENCES mandates(id), FOREIGN KEY(debt_id) REFERENCES debts(id))""")
    c.execute("CREATE TABLE IF NOT EXISTS salary_runs (run_month TEXT PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    cols = {r['name'] for r in c.execute("PRAGMA table_info(debts)")}
    for col in ("original_amount_paise", "outstanding_amount_paise", "monthly_repayment_paise"):
        if col not in cols: c.execute(f"ALTER TABLE debts ADD COLUMN {col} INTEGER")
    c.execute("""UPDATE debts SET original_amount_paise=CAST(ROUND(original_amount*100) AS INTEGER),
      outstanding_amount_paise=CAST(ROUND(outstanding_amount*100) AS INTEGER),
      monthly_repayment_paise=CAST(ROUND(monthly_repayment*100) AS INTEGER)
      WHERE original_amount_paise IS NULL OR outstanding_amount_paise IS NULL OR monthly_repayment_paise IS NULL""")
    c.commit(); c.close()


def active_bucket(c, name):
    return c.execute("SELECT id FROM buckets WHERE name=? AND active=1", (name,)).fetchone()


def amount(label):
    try:
        value = Decimal(prompt(label).strip()).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if value <= 0: raise InvalidOperation
        return value
    except (InvalidOperation, ValueError):
        print("Error: enter an amount greater than zero.")


def add_bucket(name=None):
    name = (name if name is not None else prompt("Bucket name: ")).strip().lower()
    if not name: print("Error: bucket name cannot be empty."); return
    c = db()
    try:
        c.execute("INSERT INTO buckets(name) VALUES(?)", (name,)); c.commit(); print(f"Bucket '{name}' created.")
    except sqlite3.IntegrityError: print(f"Error: bucket '{name}' already exists.")
    finally: c.close()


def show_buckets():
    c=db(); rows=c.execute("SELECT id,name FROM buckets WHERE active=1 ORDER BY id").fetchall(); c.close()
    if not rows: print("No active buckets found."); return
    print("\nBUCKETS\n" + "-"*50)
    for r in rows: print(f"{r['id']:>3}  {r['name']:<25}")
    print("-"*50)


def add_mandate():
    c=db(); print("\nAdd Mandate\n" + "-"*50)
    source=prompt("Source bucket: ", active_bucket_names).strip().lower()
    if not active_bucket(c, source): print(f"Error: bucket '{source}' does not exist."); c.close(); return
    destination=prompt("Destination: ", destination_names).strip()
    if not destination: print("Error: destination cannot be empty."); c.close(); return
    value=amount("Amount: ")
    if value is None: c.close(); return
    print(f"\nMandate:\n  From: {source}\n  To: {destination}\n  Amount: {fmt(paise(value))}")
    if prompt("\nConfirm? [y/N]: ", yes_no_options).strip().lower() != 'y': print("Mandate creation cancelled."); c.close(); return
    cur=c.execute("INSERT INTO mandates(source,destination,amount) VALUES(?,?,?)", (source,destination,float(value)))
    c.commit(); c.close(); print(f"\nMandate {mid(cur.lastrowid)} created.")


def show_mandates():
    c=db(); rows=c.execute("SELECT id,source,destination,amount FROM mandates WHERE active=1 ORDER BY id").fetchall(); c.close()
    if not rows: print("No active mandates found."); return
    print("\nACTIVE MANDATES\n" + "-"*80)
    for r in rows: print(f"{mid(r['id'])}  {r['source']:<20} → {r['destination']:<25} {fmt(paise(r['amount'])):>12}")
    print("-"*80)


def add_debt():
    c=db(); print("\nAdd Debt\n" + "-"*50)
    borrower=prompt("Borrower bucket: ", active_bucket_names).strip().lower(); lender=prompt("Lender bucket: ", active_bucket_names).strip().lower()
    if not active_bucket(c, borrower) or not active_bucket(c, lender): print("Error: borrower and lender must both be existing active buckets."); c.close(); return
    if borrower == lender: print("Error: borrower and lender cannot be the same bucket."); c.close(); return
    value=amount("Amount: ")
    if value is None: c.close(); return
    try:
        months=int(prompt("Repayment months: ").strip())
        if months <= 0: raise ValueError
    except ValueError: print("Error: repayment months must be a positive whole number."); c.close(); return
    total=paise(value); monthly=total//months
    print(f"\nDebt:\n  Borrower: {borrower}\n  Lender: {lender}\n  Amount: {fmt(total)}\n  Repayment period: {months} months\n  Monthly repayment: {fmt(monthly)} (final payment may differ)")
    if prompt("\nConfirm? [y/N]: ", yes_no_options).strip().lower() != 'y': print("Debt creation cancelled."); c.close(); return
    cur=c.execute("""INSERT INTO debts(borrower,lender,original_amount,outstanding_amount,repayment_months,monthly_repayment,
      original_amount_paise,outstanding_amount_paise,monthly_repayment_paise) VALUES(?,?,?,?,?,?,?,?,?)""",
      (borrower,lender,float(value),float(value),months,monthly/100,total,total,monthly))
    c.commit(); c.close(); print(f"\nDebt {did(cur.lastrowid)} created.")


def show_debts():
    c=db(); rows=c.execute("SELECT * FROM debts WHERE active=1 ORDER BY id").fetchall(); c.close()
    if not rows: print("No active debts found."); return
    print("\nACTIVE DEBTS\n" + "-"*120)
    for r in rows: print(f"{did(r['id'])}  {r['borrower']:<16} → {r['lender']:<16} Original: {fmt(r['original_amount_paise']):>12}  Outstanding: {fmt(r['outstanding_amount_paise']):>12}  Monthly: {fmt(r['monthly_repayment_paise']):>12}  ACTIVE")
    print("-"*120)


def salary_credited():
    password=os.environ.get("FINANCE_SALARY_PASSWORD")
    if not password: print("Salary password is not configured. Set FINANCE_SALARY_PASSWORD in your environment first."); return
    if getpass.getpass("Salary password: ") != password: print("Incorrect password."); return
    month=date.today().strftime('%Y-%m'); c=db()
    try:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT 1 FROM salary_runs WHERE run_month=?", (month,)).fetchone():
            c.rollback(); print(f"Salary for {date.today().strftime('%B %Y')} has already been processed."); return
        mandates=debts=0
        for r in c.execute("SELECT id,source,destination,amount FROM mandates WHERE active=1"):
            p=paise(r['amount']); c.execute("INSERT INTO payments(source,destination,amount,amount_paise,payment_type,mandate_id,salary_run) VALUES(?,?,?,?, 'MANDATE',?,?)", (r['source'],r['destination'],p/100,p,r['id'],month)); mandates+=1
        for r in c.execute("SELECT * FROM debts WHERE active=1 AND outstanding_amount_paise>0").fetchall():
            pending=c.execute("SELECT COALESCE(SUM(amount_paise),0) FROM payments WHERE debt_id=? AND status='OUTSTANDING'", (r['id'],)).fetchone()[0]
            available=r['outstanding_amount_paise']-pending
            if available > 0:
                p=min(r['monthly_repayment_paise'], available)
                c.execute("INSERT INTO payments(source,destination,amount,amount_paise,payment_type,debt_id,salary_run) VALUES(?,?,?,?, 'DEBT',?,?)", (r['borrower'],r['lender'],p/100,p,r['id'],month)); debts+=1
        c.execute("INSERT INTO salary_runs(run_month) VALUES(?)", (month,)); c.commit()
    except sqlite3.Error as exc: c.rollback(); print(f"Salary processing failed: {exc}"); return
    finally: c.close()
    print(f"Salary for {date.today().strftime('%B %Y')} processed successfully.\n{mandates} mandate payments generated.\n{debts} debt payments generated.")


def outstandings():
    c=db(); rows=c.execute("SELECT * FROM payments WHERE status='OUTSTANDING' ORDER BY id").fetchall(); c.close()
    if not rows: print("No outstanding payments."); return
    print("\nOUTSTANDING PAYMENTS\n" + "-"*100); total=0
    for r in rows: total+=r['amount_paise']; print(f"{pid(r['id']):<6} {r['source']:<14} {r['destination']:<25} {fmt(r['amount_paise']):>12}  {r['payment_type']}")
    print("-"*100 + f"\nTOTAL OUTSTANDING: {fmt(total)}")


def pay_outstanding():
    outstandings(); raw=prompt("\nSelect payment IDs to mark as paid: ", outstanding_payment_ids).upper().split()
    if not raw: print("No payments selected."); return
    ids=[]; invalid=[]
    for item in raw:
        if item.startswith('P') and item[1:].isdigit(): ids.append(int(item[1:]))
        else: invalid.append(item)
    c=db(); rows=[]
    for n in dict.fromkeys(ids):
        r=c.execute("SELECT * FROM payments WHERE id=?", (n,)).fetchone()
        if not r or r['status'] != 'OUTSTANDING': invalid.append(pid(n))
        else: rows.append(r)
    if invalid: print("Invalid or already paid payment IDs: " + ', '.join(invalid))
    if not rows: c.close(); return
    print("Selected: " + ', '.join(pid(r['id']) for r in rows))
    if prompt("Confirm? [y/N]: ", yes_no_options).strip().lower() != 'y': print("Payment update cancelled."); c.close(); return
    try:
        c.execute("BEGIN")
        for r in rows:
            c.execute("UPDATE payments SET status='PAID',paid_at=CURRENT_TIMESTAMP WHERE id=?", (r['id'],))
            if r['debt_id'] is not None:
                d=c.execute("SELECT outstanding_amount_paise FROM debts WHERE id=?", (r['debt_id'],)).fetchone()
                remain=max(0,d['outstanding_amount_paise']-r['amount_paise'])
                c.execute("UPDATE debts SET outstanding_amount_paise=?,outstanding_amount=?,active=? WHERE id=?", (remain,remain/100,0 if remain==0 else 1,r['debt_id']))
        c.commit()
    except sqlite3.Error as exc: c.rollback(); print(f"Payment update failed: {exc}"); return
    finally: c.close()
    for r in rows: print(f"{pid(r['id'])} marked as PAID.")


def active_row(c, table, prefix):
    choices = active_mandate_ids if prefix == 'M' else active_debt_ids
    raw=prompt(f"{prefix} ID to remove: ", choices).strip().upper()
    if raw.startswith(prefix): raw=raw[1:]
    if not raw.isdigit(): print(f"Error: enter a valid {prefix} ID."); return
    row=c.execute(f"SELECT * FROM {table} WHERE id=? AND active=1", (int(raw),)).fetchone()
    if not row: print(f"Error: active {prefix}{int(raw):03d} not found.")
    return row


def remove_mandate():
    c=db(); r=active_row(c,'mandates','M')
    if r and prompt(f"Disable {mid(r['id'])}? Existing payments stay intact. [y/N]: ", yes_no_options).strip().lower()=='y': c.execute("UPDATE mandates SET active=0 WHERE id=?",(r['id'],)); c.commit(); print(f"{mid(r['id'])} disabled.")
    elif r: print("Removal cancelled.")
    c.close()


def remove_debt():
    c=db(); r=active_row(c,'debts','D')
    if not r: c.close(); return
    warn=f" It has {fmt(r['outstanding_amount_paise'])} outstanding; future repayments will stop." if r['outstanding_amount_paise'] else ''
    if prompt(f"Cancel {did(r['id'])}? Historical payments stay intact.{warn} [y/N]: ", yes_no_options).strip().lower()=='y': c.execute("UPDATE debts SET active=0 WHERE id=?",(r['id'],)); c.commit(); print(f"{did(r['id'])} cancelled.")
    else: print("Removal cancelled.")
    c.close()


def can_deactivate(c, r):
    name=r['name']
    if c.execute("SELECT 1 FROM mandates WHERE source=? AND active=1",(name,)).fetchone() or c.execute("SELECT 1 FROM debts WHERE active=1 AND (borrower=? OR lender=?)",(name,name)).fetchone(): return False
    return True


def remove_bucket():
    c=db(); name=prompt("Bucket name to remove: ", active_bucket_names).strip().lower(); r=c.execute("SELECT * FROM buckets WHERE name=? AND active=1",(name,)).fetchone()
    if not r: print(f"Error: active bucket '{name}' not found.")
    elif not can_deactivate(c,r): print(f"Cannot remove '{name}': it has active mandates or debts.")
    elif prompt(f"Deactivate '{name}'? Historical references stay intact. [y/N]: ", yes_no_options).strip().lower()=='y': c.execute("UPDATE buckets SET active=0 WHERE id=?",(r['id'],)); c.commit(); print(f"Bucket '{name}' deactivated.")
    else: print("Removal cancelled.")
    c.close()


def remove_buckets():
    show_buckets()
    if prompt("Deactivate all safe active buckets? [y/N]: ", yes_no_options).strip().lower()!='y': print("Bulk removal cancelled."); return
    c=db(); rows=c.execute("SELECT * FROM buckets WHERE active=1 ORDER BY id").fetchall()
    for r in rows:
        if can_deactivate(c,r): c.execute("UPDATE buckets SET active=0 WHERE id=?",(r['id'],)); print(f"Bucket '{r['name']}' deactivated.")
        else: print(f"Skipped '{r['name']}': it has active mandates or debts.")
    c.commit(); c.close()


def show_payments():
    c=db(); rows=c.execute("SELECT * FROM payments ORDER BY id").fetchall(); c.close()
    if not rows: print("No payments found."); return
    print("\nPAYMENT HISTORY\n" + "-"*120)
    for r in rows: print(f"{pid(r['id']):<6} {r['created_at'][:10]:<12} {r['source']:<14} {r['destination']:<25} {fmt(r['amount_paise']):>12}  {r['payment_type']:<8} {r['status']}")
    print("-"*120)


def main():
    initialize_db(); parser=argparse.ArgumentParser(description='Personal Finance Mandate System'); subs=parser.add_subparsers(dest='command',required=True)
    p=subs.add_parser('add_bucket',help='Create a bucket'); p.add_argument('name',nargs='?',help='Bucket name (otherwise prompted)')
    names=('salary_credited','outstandings','show_buckets','add_mandate','add_debt','remove_mandate','show_mandates','remove_debt','show_payments','remove_buckets','remove_bucket','show_debts','pay_outstanding','cli')
    for name in names: subs.add_parser(name)
    args=parser.parse_args()
    if args.command=='add_bucket': add_bucket(args.name); return
    if args.command=='cli': interactive_cli(); return
    {'salary_credited':salary_credited,'outstandings':outstandings,'show_buckets':show_buckets,'add_mandate':add_mandate,'add_debt':add_debt,'remove_mandate':remove_mandate,'show_mandates':show_mandates,'remove_debt':remove_debt,'show_payments':show_payments,'remove_buckets':remove_buckets,'remove_bucket':remove_bucket,'show_debts':show_debts,'pay_outstanding':pay_outstanding}[args.command]()


if __name__ == '__main__': main()
