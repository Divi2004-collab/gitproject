from pathlib import Path
import csv
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import CellIsRule
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "excel" / "cheque_register_template.xlsx"
OUT.parent.mkdir(exist_ok=True)
with (ROOT / "data" / "cheques.csv").open(encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

wb = Workbook()
summary = wb.active
summary.title = "Overview"
ws = wb.create_sheet("Cheque Register")
navy, teal, pale, white = "12304A", "087E8B", "EAF3F5", "FFFFFF"
summary.sheet_view.showGridLines = False
summary.merge_cells("A1:D1")
summary["A1"] = "CHEQUE DISHONOUR & RECOVERY"
summary["A1"].font = Font(size=18, bold=True, color=white)
summary["A1"].fill = PatternFill("solid", fgColor=navy)
summary["A1"].alignment = Alignment(vertical="center")
summary.row_dimensions[1].height = 34
summary.append(["Metric", "Value", "How it is calculated", ""])
for c in summary[2]:
    c.font = Font(bold=True, color=white); c.fill = PatternFill("solid", fgColor=teal)
metrics = [
    ("Cheque count", "=COUNTA('Cheque Register'!A2:A201)", "Number of populated cheque records"),
    ("Dishonoured cheques", '=COUNTIF(\'Cheque Register\'!J2:J201,"Bounced")+COUNTIF(\'Cheque Register\'!J2:J201,"Partially recovered")', "Bounced or partially recovered status"),
    ("Bounce rate", "=IFERROR(B4/B3,0)", "Dishonoured cheques / cheque count"),
    ("Cheque value", "=SUM('Cheque Register'!H2:H201)", "Total face value"),
    ("Recovered", "=SUM('Cheque Register'!N2:N201)", "Total recovered amount"),
    ("Outstanding", "=SUM('Cheque Register'!O2:O201)", "Face value less recovery"),
    ("Overdue 30+", '=COUNTIF(\'Cheque Register\'!L2:L201,">30")', "Open items over 30 days past due"),
    ("Overdue 60+", '=COUNTIF(\'Cheque Register\'!L2:L201,">60")', "Open items over 60 days past due"),
    ("Overdue 90+", '=COUNTIF(\'Cheque Register\'!L2:L201,">90")', "Open items over 90 days past due"),
]
for label, formula, desc in metrics:
    summary.append([label, formula, desc, None])
summary["B5"].number_format = "0.0%"
for cell in ["B6", "B7", "B8"]: summary[cell].number_format = '₹#,##0.00'
summary.column_dimensions["A"].width = 25
summary.column_dimensions["B"].width = 20
summary.column_dimensions["C"].width = 48
summary.freeze_panes = "A3"

headers = ["Cheque ID", "Cheque No", "Issue Date", "Due Date", "Customer ID", "Account ID", "Branch", "Amount", "Bounce Date", "Status", "Bounce Reason", "Days Past Due", "Aging Bucket", "Recovered Amount", "Outstanding", "Last Follow-up", "Next Action / Notes"]
ws.append(headers)
for r in rows:
    ws.append([r["cheque_id"], r["cheque_no"], r["issue_date"], r["due_date"], r["customer_id"], r["account_id"], r["branch"], float(r["amount"]), r["bounce_date"] or None, r["status"], r["bounce_reason"] or None,
              f'=IF(D{ws.max_row+1}="","",MAX(0,TODAY()-D{ws.max_row+1}))',
              f'=IF(L{ws.max_row+1}="","",IF(L{ws.max_row+1}<=30,"0-30 days",IF(L{ws.max_row+1}<=60,"31-60 days",IF(L{ws.max_row+1}<=90,"61-90 days","90+ days"))))',
              float(r["recovered_amount"]), f'=MAX(0,H{ws.max_row+1}-N{ws.max_row+1})', r["last_follow_up"] or None, None])
for row in range(ws.max_row + 1, 202):
    ws.cell(row, 12, f'=IF(D{row}="","",MAX(0,TODAY()-D{row}))')
    ws.cell(row, 13, f'=IF(L{row}="","",IF(L{row}<=30,"0-30 days",IF(L{row}<=60,"31-60 days",IF(L{row}<=90,"61-90 days","90+ days"))))')
    ws.cell(row, 15, f'=IF(H{row}="","",MAX(0,H{row}-N{row}))')
tab = Table(displayName="ChequeRegister", ref="A1:Q201")
tab.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showFirstColumn=False, showLastColumn=False, showRowStripes=True, showColumnStripes=False)
ws.add_table(tab)
ws.freeze_panes = "A2"
ws.auto_filter.ref = "A1:Q201"
widths = [14, 13, 13, 13, 15, 17, 27, 16, 14, 22, 24, 16, 18, 19, 17, 16, 32]
for idx, width in enumerate(widths, 1): ws.column_dimensions[get_column_letter(idx)].width = width
for row in ws.iter_rows(min_row=2, max_row=201):
    for c in row:
        c.alignment = Alignment(vertical="center")
        if c.column in (3, 4, 9, 16): c.number_format = "yyyy-mm-dd"
        if c.column in (8, 14, 15): c.number_format = '₹#,##0.00'
dv = DataValidation(type="list", formula1='"Pending,Bounced,Partially recovered,Recovered,Disputed"', allow_blank=True)
ws.add_data_validation(dv); dv.add("J2:J201")
ws.conditional_formatting.add("L2:L201", CellIsRule(operator="greaterThan", formula=["90"], fill=PatternFill("solid", fgColor="F4CCCC")))
ws.conditional_formatting.add("O2:O201", CellIsRule(operator="greaterThan", formula=["0"], fill=PatternFill("solid", fgColor="FFF2CC")))
for sh in (summary, ws):
    sh.sheet_properties.pageSetUpPr.fitToPage = True
wb.calculation.fullCalcOnLoad = True
wb.calculation.forceFullCalc = True
wb.calculation.calcMode = "auto"
wb.save(OUT)
# Reopen check: workbook structure and formulas survived serialization.
check = load_workbook(OUT, data_only=False)
assert check.sheetnames == ["Overview", "Cheque Register"]
assert check["Cheque Register"]["L2"].value.startswith("=IF(")
assert check["Overview"]["B5"].value == "=IFERROR(B4/B3,0)"
print("Workbook created and formulas verified.")
