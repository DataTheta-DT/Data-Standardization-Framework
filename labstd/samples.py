import os

CSV_TEXT = """Patient ID,Patient Name,Sex,DOB,Collected,Test,Result,Unit,Reference Range,Flag
SYN-2001,John Smith,M,1978-02-03,2026-10-29 07:45,Glucose,5.4,mmol/L,3.9 - 5.6,
SYN-2001,John Smith,M,1978-02-03,2026-10-29 07:45,Creatinine,0.9,mg/dL,0.7 - 1.3,
SYN-2001,John Smith,M,1978-02-03,2026-10-29 07:45,Sodium,140,mmol/L,135 - 145,
SYN-2001,John Smith,M,1978-02-03,2026-10-29 07:45,Potassium,4.1,mmol/L,3.5 - 5.1,
SYN-2001,John Smith,M,1978-02-03,2026-10-29 07:45,Total Cholesterol,212,mg/dL,< 200,H
SYN-2001,John Smith,M,1978-02-03,2026-10-29 07:45,Triglycerides,150,mg/dL,< 150,
SYN-2001,John Smith,M,1978-02-03,2026-10-29 07:45,TSH,2.1,m[IU]/L,0.4 - 4.0,
SYN-2002,Priya N,F,1990-09-21,2026-10-29 08:10,Hemoglobin,13.4,g/dL,12.0 - 15.5,
SYN-2002,Priya N,F,1990-09-21,2026-10-29 08:10,Glucose,98,,70 - 100,
SYN-2002,Priya N,F,1990-09-21,2026-10-29 08:10,Alanine aminotransferase,32,U/L,7 - 55,
SYN-2002,Priya N,F,1990-09-21,2026-10-29 08:10,Mystery Marker 77,3.2,units,1 - 5,
"""

HL7_TEXT = "\r".join([
    "MSH|^~\\&|LIS|CityLab|EMR|Hosp|20261030093000||ORU^R01|MSG0001|P|2.5",
    "PID|1||SYN-3001^^^CityLab||Kumar^Arun||19700615|M",
    "OBR|1|||CBC^Complete Blood Count|||20261030081500",
    "OBX|1|NM|718-7^Hemoglobin^LN||14.1|g/dL|13.0-17.0|N|||F",
    "OBX|2|NM|6690-2^WBC^LN||6.9|10*3/uL|4.0-11.0|N|||F",
    "OBX|3|NM|2345-7^Glucose^LN||132|mg/dL|70-99|H|||F",
    "OBX|4|NM|LOC123^Special Marker^L||5.0|U|1-3|H|||F",
    "OBX|5|NM|1742-6^ALT^LN||48|U/L|7-55|N|||F",
])

PDF_ROWS = [
    ("Test", "Result", "Unit", "Reference Range", "Flag"),
    ("Hemoglobin", "11.2", "g/dL", "12.0 - 15.5", "L"),
    ("Total WBC Count", "7800", "/cumm", "4000 - 11000", ""),
    ("Platelet Count", "1.5", "lakhs/cumm", "1.5 - 4.1", ""),
    ("RBC Count", "4.1", "million/cumm", "3.8 - 5.2", ""),
    ("Hematocrit (PCV)", "34.5", "%", "36 - 46", "L"),
    ("Mean Corpuscular Volume (MCV)", "82", "fL", "80 - 100", ""),
    ("Mean Corpuscular Hemoglobin (MCH)", "27", "pg", "27 - 33", ""),
    ("Total Cholesterol", "212", "mg/dL", "< 200", "H"),
    ("HDL Cholesterol", "48", "mg/dL", "> 40", ""),
    ("Mystery Marker 77", "3.2", "units", "1 - 5", ""),
]

SCAN_ROWS = [
    ("Test", "Result", "Unit", "Reference Range"),
    ("Hemoglobin", "13.4", "g/dL", "12.0 - 15.5"),
    ("Total Cholesterol", "182", "mg/dL", "< 200"),
    ("Serum Creatinine", "0.9", "mg/dL", "0.6 - 1.1"),
    ("Potassium", "4.1", "mmol/L", "3.5 - 5.1"),
    ("Sodium", "140", "mmol/L", "135 - 145"),
]


def write_pdf(path):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    st = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=A4)
    t = Table(PDF_ROWS, colWidths=[150, 60, 90, 100, 40])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
    doc.build([
        Paragraph("City Diagnostics Laboratory", st["Title"]),
        Paragraph("Patient Name: Anita Rao &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; Patient ID: SYN-1001 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; Sex: F", st["Normal"]),
        Paragraph("DOB: 1992-04-12 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; Collected: 2026-10-28 08:30", st["Normal"]),
        Spacer(1, 14),
        t,
    ])


def _font(size):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def write_scan(path, name, pid, collected, rows=SCAN_ROWS, as_pdf=False):
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("L", (1500, 900), 255)
    d = ImageDraw.Draw(img)
    f, fb = _font(30), _font(34)
    d.text((60, 40), "Sunrise Pathology Lab", font=fb, fill=0)
    d.text((60, 110), f"Patient Name: {name}", font=f, fill=0)
    d.text((620, 110), f"Patient ID: {pid}", font=f, fill=0)
    d.text((1000, 110), "Sex: F", font=f, fill=0)
    d.text((60, 160), f"Collected: {collected}", font=f, fill=0)
    xs = [60, 620, 860, 1100]
    y = 260
    for i, r in enumerate(rows):
        for x, c in zip(xs, r):
            d.text((x, y), c, font=fb if i == 0 else f, fill=0)
        y += 70
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    if as_pdf:
        img.convert("RGB").save(path, "PDF", resolution=150)
    else:
        img.save(path)


def write_all(out_dir):
    import shutil
    import tempfile
    os.makedirs(out_dir, exist_ok=True)
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "synthetic_chemistry_panel.csv"), "w", newline="") as f:
        f.write(CSV_TEXT)
    with open(os.path.join(tmp, "synthetic_oru_message.hl7"), "w", newline="") as f:
        f.write(HL7_TEXT)
    write_pdf(os.path.join(tmp, "synthetic_cbc_digital.pdf"))
    write_scan(os.path.join(tmp, "synthetic_scan.png"), "Meera S", "SYN-4001", "29-Oct-2026")
    write_scan(os.path.join(tmp, "synthetic_scan.jpg"), "Ravi K", "SYN-4003", "31-Oct-2026")
    write_scan(os.path.join(tmp, "synthetic_scan_image_only.pdf"), "Anita R", "SYN-4002", "30-Oct-2026", as_pdf=True)
    for name in sorted(os.listdir(tmp)):
        dst = os.path.join(out_dir, name)
        if not os.path.exists(dst):
            shutil.copyfile(os.path.join(tmp, name), dst)
    shutil.rmtree(tmp, ignore_errors=True)
    return sorted(os.listdir(out_dir))
