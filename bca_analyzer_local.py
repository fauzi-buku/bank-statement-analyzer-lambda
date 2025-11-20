# bca_analyzer_local.py
# Standalone CLI version - accepts PDF file path as argument

import fitz
import pandas as pd
import re
from collections import defaultdict
import io
import os
import argparse
import sys
from pathlib import Path

# ============== General Helpers ==============

def safe_filename(text: str, default: str = "BCA_Statement_Analysis") -> str:
    if not text or str(text).strip().lower() in {"none", "nan", "nat"}:
        text = default
    text = str(text)
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    text = re.sub(r"\s+", "_", text).strip("_")
    text = re.sub(r"[^A-Za-z0-9._-]", "", text)
    return text[:120] if len(text) > 120 else text

# ---------------------- PDF Processing Functions ---------------------- #
def extract_text_from_pdf(file_bytes):
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        return "\\n".join([page.get_text() for page in doc])

def clean_statement_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]

def convert_to_int(amount_str):
    if amount_str is None:
        return None
    try:
        cleaned = amount_str.replace(",", "")
        match = re.match(r"^(\\d+\\.\\d{2})", cleaned)
        return float(match.group(1)) if match else float(cleaned)
    except:
        return None

def extract_personal_info(text):
    lines = clean_statement_lines(text)
    info = {"Bank": "BCA", "Account Name": None, "Account Number": None, "Branch": None, "Period": None, "Currency": None}

    for i, line in enumerate(lines):
        if line.upper().startswith("KCP "):
            info["Branch"] = line.strip()
            for j in range(i+1, len(lines)):
                if lines[j].strip():
                    info["Account Name"] = lines[j].strip()
                    break
            break

    for i, line in enumerate(lines):
        line_upper = line.upper()
        if "NO. REKENING" in line_upper and not info["Account Number"]:
            if i + 2 < len(lines):
                possible_number = lines[i + 2].strip()
                if re.fullmatch(r"\d{6,}", possible_number):
                    info["Account Number"] = possible_number

        if "PERIODE" in line_upper and not info["Period"]:
            if i + 2 < len(lines):
                possible_period = lines[i + 2].strip()
                if re.match(r"[A-Z]+\s+\d{4}", possible_period, re.IGNORECASE):
                    info["Period"] = possible_period

        if "MATA UANG" in line_upper and not info["Currency"]:
            if i + 2 < len(lines):
                possible_currency = lines[i + 2].strip()
                if re.match(r"[A-Z]{2,4}", possible_currency):
                    info["Currency"] = possible_currency
    return info

def extract_monthly_summary(text):
    summary = {
        "Saldo Awal": None,
        "Saldo Akhir": None,
        "Difference": None,
        "Mutasi Kredit": None,
        "Mutasi Debet": None,
    }

    footer_text = "\n".join(text.splitlines()[-50:])
    patterns = {
        "Saldo Awal": r"SALDO AWAL\s*:?[\n\s]*([\d.,]+)",
        "Saldo Akhir": r"SALDO AKHIR\s*:?[\n\s]*([\d.,]+)",
        "Mutasi Kredit": r"MUTASI CR\s*:?[\n\s]*([\d.,]+)",
        "Mutasi Debet": r"MUTASI DB\s*:?[\n\s]*([\d.,]+)",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, footer_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            val = round(convert_to_int(match.group(1)), 2)
            summary[key] = int(val) if "Count" in key else val

    # Calculate difference only if both values are not None
    if summary['Saldo Akhir'] is not None and summary['Saldo Awal'] is not None:
        summary['Difference'] = round(abs(summary['Saldo Akhir'] - summary['Saldo Awal']), 2)
    else:
        summary['Difference'] = None

    return summary

def extract_partner_name(description):

   skip_keywords = ["BIAYA", "ADM", "BUNGA", "PAJAK", "KLIRING", "TARIK TUNAI", "SETORAN", "BI-FAST"]
   if any(keyword in description.upper() for keyword in skip_keywords):
       return None

   cleaned = description.strip()
   cleaned = re.sub(r"BERSAMBUNG.*", "", cleaned, flags=re.IGNORECASE)
   cleaned = re.sub(r"REKENING.*", "", cleaned, flags=re.IGNORECASE)
   cleaned = re.sub(r"KCP.*", "", cleaned, flags=re.IGNORECASE)
   cleaned = re.sub(r"HALAMAN.*", "", cleaned, flags=re.IGNORECASE)
   cleaned = re.sub(r"INDONESIA.*", "", cleaned, flags=re.IGNORECASE)
   cleaned = re.sub(r"\b(DB|CR)\b", "", cleaned)
   cleaned = re.sub(r"\s+", " ", cleaned).strip()

   if "TRSF E-BANKING" in cleaned.upper():
       after_trsf = re.sub(r'^TRSF E-BANKING\s+\d+/[A-Z]+/\w+\s*', '', cleaned, flags=re.IGNORECASE)
       after_trsf = re.sub(r'REF:\w+\s*', '', after_trsf)
       after_trsf = re.sub(r'DC\s+\d+\s*', '', after_trsf)

       # Alphanumeric separator (S202306000045)
       alphanumeric_match = re.search(r'\b[A-Z]+\d{6,}\w*\b', after_trsf)
       if alphanumeric_match:
           partner_text = after_trsf[alphanumeric_match.end():].strip()
           if partner_text:
               alphabetic_words = [word for word in partner_text.split()
                                  if word.isalpha() and len(word) >= 1]
               if alphabetic_words:
                   return ' '.join(alphabetic_words)

       # Remove noise patterns and take end
       noise_patterns = [
           r'titip\s+\d+\s*',
           r'transaksi\s+\d+\s+\w+\s+\w+\s+\d{4}\s*',
           r'MARKETING\s+SUPPORT\s+\w+\s+\w+\s+\d{4}\s*',
           r'\w+\s+\d{8}\s*',
           r'BSML\s+\w+\s*'
       ]

       for pattern in noise_patterns:
           after_trsf = re.sub(pattern, '', after_trsf, flags=re.IGNORECASE)

       alphabetic_words = [word for word in after_trsf.split()
                          if word.isalpha() and len(word) >= 2]

       if alphabetic_words:
           return ' '.join(alphabetic_words[-3:] if len(alphabetic_words) >= 3 else alphabetic_words)

   elif "KR OTOMATIS" in cleaned.upper():
       after_kr = None

       # Handle RTGS pattern
       if "RTGS-PT. BANK" in cleaned:
           after_kr = re.sub(r'^KR OTOMATIS RTGS-PT\. BANK [A-Z\s]+ BRINIDJA/\d+\s*', '', cleaned, flags=re.IGNORECASE)

       # Handle LLG pattern
       elif "LLG-" in cleaned:
           after_kr = re.sub(r'^KR OTOMATIS\s+LLG-[A-Z]+\s*', '', cleaned, flags=re.IGNORECASE)

       if after_kr:
           # Priority: Split by TRX (for RTGS cases)
           if "TRX" in after_kr:
               before_trx = after_kr.split("TRX")[0].strip()
               alphabetic_words = [word for word in before_trx.split()
                                  if word.isalpha() and len(word) >= 1]
               if alphabetic_words:
                   return ' '.join(alphabetic_words)

           # Split by BSML
           bsml_match = re.search(r'\s+BSML\s+\w+', after_kr)
           if bsml_match:
               partner_text = after_kr[:bsml_match.start()].strip()
               alphabetic_words = [word for word in partner_text.split()
                                  if word.isalpha() and len(word) >= 1]
               if alphabetic_words:
                   return ' '.join(alphabetic_words)

           # Split by pipe
           pipe_match = re.search(r'\s*\|\s*\d+', after_kr)
           if pipe_match:
               partner_text = after_kr[:pipe_match.start()].strip()
               alphabetic_words = [word for word in partner_text.split()
                                  if word.isalpha() and len(word) >= 1]
               if alphabetic_words:
                   return ' '.join(alphabetic_words)

           # Split by end numbers
           end_numbers_match = re.search(r'\s+\d{4}\s*$', after_kr)
           if end_numbers_match:
               partner_text = after_kr[:end_numbers_match.start()].strip()
               partner_text = re.sub(r'BP\d+\s+\d+BP\d+\s+\d+\s+-\d+\s*', '', partner_text)
               alphabetic_words = [word for word in partner_text.split()
                                  if word.isalpha() and len(word) >= 2]
               if alphabetic_words:
                   return ' '.join(alphabetic_words)

   return None

def extract_transactions(text):
    lines = clean_statement_lines(text)
    transactions = []
    buffer = []

    for line in lines:
        if re.match(r"^\d{2}/\d{2}$", line):
          # Skip jika ini tanggal lengkap (dd/mm/yyyy)
          if re.match(r"^\d{2}/\d{2}/\d{4}$", line.strip()):
              if buffer:
                  buffer.append(line)
          # Skip jika ini bulan/tahun (mm/yyyy)
          elif re.match(r"^\d{2}/\d{4}$", line.strip()):
              # Contoh: "01/2025" - ini bulan/tahun, bukan transaksi
              if buffer:
                  buffer.append(line)
          # Skip jika dimulai dengan dd/mm tapi sepertinya referensi/ID bukan transaksi baru
          elif re.match(r"^\d{2}/\d{2}\s+(WSID:|REF:|ID:|TXN:|\w+:|/\w+)", line, re.IGNORECASE):
              # Contoh: "04/01 WSID:Z8351" atau "01/02 /Z83000" - ini referensi, bukan transaksi baru
              if buffer:
                  buffer.append(line)
          # Cek apakah ini benar-benar transaksi baru (dd/mm + kata kunci transaksi)
          elif re.search(r"^\d{2}/\d{2}\s+(SETORAN|TRSF|TRANSFER|OTOMATIS|TARIK|KLIRING|BUNGA|BIAYA|KOREKSI|ADM)", line, re.IGNORECASE):
              # Ini transaksi baru yang valid
              if buffer:
                  transactions.append(" ".join(buffer).strip())
                  buffer = []
              buffer.append(line)
          else:
              # Default: jika dimulai dd/mm tapi tidak jelas, anggap sebagai transaksi baru
              if buffer:
                  transactions.append(" ".join(buffer).strip())
                  buffer = []
              buffer.append(line)
        elif buffer:
          buffer.append(line)

    if buffer:
        last_tx = " ".join(buffer).strip()
        last_tx_cleaned = re.split(r"SALDO AWAL\s*:", last_tx, flags=re.IGNORECASE)[0].strip()
        transactions.append(last_tx_cleaned)

    parsed = []
    saldo_awal = None
    current_balance = 0

    for record in transactions:
        date_match = re.match(r"^(\d{2}/\d{2})", record)
        date = date_match.group(1) if date_match else None
        clean_record = record.replace(",", "")

        all_numbers = re.findall(r"\b(?:\d{1,3}(?:,\d{3})+|\d{4,})\.\d{2}\b", clean_record)
        all_unique = list(dict.fromkeys(all_numbers))
        amount_str = all_unique[-2] if len(all_unique) >= 2 else (
            all_unique[-1] if len(all_unique) == 1 else None)
        ending_balance_str = all_unique[-1] if all_unique else None

        # Konversi ke integer
        amount = convert_to_int(amount_str)
        ending_balance = convert_to_int(ending_balance_str)
        txn_type_match = re.search(r"\b(DB|CR)\b", record)
        txn_type = txn_type_match.group(1) if txn_type_match else None

        description = re.sub(r"\d{2}/\d{2}", "", record)
        description = re.sub(r"\b(DB|CR)\b", "", description)
        description = re.sub(r"(\d{1,3}(?:,\d{3})*|\d+)\.\d{2}", "", description)
        description = re.sub(r"\s{2,}", " ", description).strip()

        # Remove known footer/header fragments
        noise_patterns = [
            r"Bersambung ke halaman berikut",
            r"REKENING GIRO.*",
            r"NO\.?\s*REKENING\s*:? .*",
            r"PERIODE\s*:? .*",
            r"MATA UANG\s*:? .*",
            r"HALAMAN\s*:? .*",
            r"INDONESIA",
            r"CATATAN:.*",
            r"TANGGAL KETERANGAN.*",
            r"\d{1,2}\s*/\s*\d{2,}"  # page X / Y
        ]
        for pat in noise_patterns:
            description = re.sub(pat, "", description, flags=re.IGNORECASE)
        description = re.sub(r"\s{2,}", " ", description).strip()
        partner_name = extract_partner_name(description)

        # Check if SALDO AWAL
        if "SALDO AWAL" in description.upper():
            saldo_awal = amount if amount is not None else 0
            current_balance = saldo_awal
            calculated_ending_balance = round(current_balance, 2)
            parsed.append({
                "Date": date,
                "Description": description,
                "Amount": amount,
                "Transaction Type": None,
                "Ending Balance": calculated_ending_balance,
                "Partner": None
            })
            continue

        # If not SALDO AWAL, count ending balance
        calculated_ending_balance = round(current_balance, 2)
        is_credit = False
        is_debit = False
        if amount is not None:

            is_credit = False
            is_debit = False
            # 1. Cek berdasarkan txn_type dari PDF
            if txn_type == "CR":
                is_credit = True
            elif txn_type == "DB":
                is_debit = True
            # 2. Cek berdasarkan description (override txn_type jika perlu)
            description_upper = description.upper()

            credit_keywords = [
                "SETORAN",
                "TRANSFER MASUK",
                "KLIRING MASUK",
                "BUNGA",
                "KOREKSI KREDIT",
                "TRSF E-BANKING CR",
                "E-BANKING CR",
                "TRANSFER CR",
                "GIRO MASUK",
                "OTOMATIS"
            ]

            debit_keywords = [
                "TARIK TUNAI",
                "TRANSFER KELUAR",
                "KLIRING KELUAR",
                "BIAYA ADM",
                "ADM",
                "KOREKSI DEBET",
                "TRSF E-BANKING DB",
                "E-BANKING DB",
                "TRANSFER DB",
                "B.ADM",
                "PAJAK BUNGA"
            ]

            # Cek credit keywords
            for keyword in credit_keywords:
                if keyword in description_upper:
                    is_credit = True
                    is_debit = False
                    break

            # Cek debit keywords (prioritas lebih tinggi dari credit)
            for keyword in debit_keywords:
                if keyword in description_upper:
                    is_debit = True
                    is_credit = False
                    break

            # Count balance based on transaction type
            if is_credit:
                current_balance += amount
            elif is_debit:
                current_balance -= amount
            else:
                # Jika tidak bisa ditentukan, gunakan txn_type default atau skip
                print(f"Warning: Cannot determine transaction type for: {description}")
            calculated_ending_balance = round(current_balance, 2)

        parsed.append({
            "Date": date,
            "Description": description,
            "Amount": amount,
            "Transaction Type": "CR" if is_credit else ("DB" if is_debit else None),
            "Ending Balance": calculated_ending_balance,
            "Partner": partner_name
        })
    return parsed

def additional_analytics(transactions_df, summary_df):

    analytics = {
        'No_of_Credit': 0,
        'No_of_Debit': 0,
        'Total_Credit_Amount': 0.0,
        'Total_Debit_Amount': 0.0,
        'Total_Partners': 0,
        'Top_Partner': None,
        'Top_Partner_Amount': 0.0
    }

    # Check if DataFrame is empty or missing required columns
    if transactions_df.empty or 'Description' not in transactions_df.columns:
        return analytics

    begin_balance = None
    end_balance = None

    # Filter out SALDO AWAL transactions for analysis
    regular_transactions = transactions_df[
        ~transactions_df['Description'].str.upper().str.contains('SALDO AWAL', na=False)
    ]

    # 2. No. of Credit: Berapa kali transaksi credit dilakukan
    credit_transactions = regular_transactions[
        regular_transactions['Transaction Type'] == 'CR'
    ]
    analytics['No_of_Credit'] = len(credit_transactions)

    # 3. No. of Debit: Berapa kali transaksi debit dilakukan
    debit_transactions = regular_transactions[
        regular_transactions['Transaction Type'] == 'DB'
    ]
    analytics['No_of_Debit'] = len(debit_transactions)

    # 4. Total amount transaksi credit
    total_credit_amount = credit_transactions['Amount'].sum()
    analytics['Total_Credit_Amount'] = round(total_credit_amount, 2) if pd.notna(total_credit_amount) else 0.0

    # 5. Total amount transaksi debit
    total_debit_amount = debit_transactions['Amount'].sum()
    analytics['Total_Debit_Amount'] = round(total_debit_amount, 2) if pd.notna(total_debit_amount) else 0.0

    # 6. Partner analysis (tujuan transfer) untuk credit transactions
    partners_credit = defaultdict(float)
    i = 0
    for _, txn in credit_transactions.iterrows():
        partner = txn['Partner']
        amount = txn['Amount']
        if partner and pd.notna(amount):
            partners_credit[partner] += amount

    # Convert to sorted list of tuples (partner, total_amount)
    partners_sorted = sorted(partners_credit.items(), key=lambda x: x[1], reverse=True)

    # Summary stats for partners
    analytics['Total_Partners'] = len(partners_sorted)
    analytics['Top_Partner'] = partners_sorted[0][0] if partners_sorted else None
    analytics['Top_Partner_Amount'] = round(partners_sorted[0][1], 2) if partners_sorted else 0.0

    return analytics

def extract_partner_transactions(transactions):

    if isinstance(transactions, list):
        if not transactions:  # Handle empty list
            return []
        transactions_df = pd.DataFrame(transactions)
    else:
        transactions_df = transactions

    # Check if DataFrame is empty
    if transactions_df.empty:
        return []

    # Filter out SALDO AWAL transactions
    regular_transactions = transactions_df[
        ~transactions_df['Description'].str.upper().str.contains('SALDO AWAL', na=False)
    ]

    # Filter transactions yang ada partner name
    partner_transactions = regular_transactions[
        (regular_transactions['Partner'].notna()) &
        (regular_transactions['Partner'].astype(str).str.strip() != '') &
        (regular_transactions['Partner'].astype(str) != 'nan')
    ]

    if partner_transactions.empty:
        return []

    partner_summary = []

    # Group by partner
    for partner in partner_transactions['Partner'].unique():
        if pd.isna(partner) or str(partner).strip() == '':
            continue

        partner_txns = partner_transactions[partner_transactions['Partner'] == partner]

        # Credit transactions
        credit_txns = partner_txns[partner_txns['Transaction Type'] == 'CR']
        total_credit = float(credit_txns['Amount'].fillna(0).sum()) if not credit_txns.empty else 0.0

        # Debit transactions
        debit_txns = partner_txns[partner_txns['Transaction Type'] == 'DB']
        total_debit = float(debit_txns['Amount'].fillna(0).sum()) if not debit_txns.empty else 0.0

        # Add to summary
        partner_summary.append({
            'Partner': str(partner),
            'Total_Credit': round(total_credit, 2),
            'Total_Debit': round(total_debit, 2),
            'Credit_Count': len(credit_txns),
            'Debit_Count': len(debit_txns),
            'Total_Transactions': len(partner_txns)
        })

    # Sort by total activity
    if partner_summary:
        partner_summary.sort(key=lambda x: x['Total_Credit'], reverse=True)

    return partner_summary

def parse_bca_statement(pdf_path):
    full_text = extract_text_from_pdf(pdf_path)
    personal_info = extract_personal_info(full_text)
    summary_info = extract_monthly_summary(full_text)
    transactions = extract_transactions(full_text)
    partner_transactions = extract_partner_transactions(transactions)

    personal_df = pd.DataFrame([personal_info])
    summary_df = pd.DataFrame([summary_info])
    trx_df = pd.DataFrame(transactions)
    partner_trx_df = pd.DataFrame(partner_transactions)

    # Calculate additional analytics
    analytics = additional_analytics(trx_df, summary_df)
    analytics_df = pd.DataFrame([analytics])

    return personal_df, summary_df, trx_df, partner_trx_df, analytics_df

# ============== CLI Interface ==============

def create_excel_file(personal_df, summary_df, trx_df, partner_trx_df, analytics_df, output_path):
    """Create Excel file with all analysis sheets"""
    with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
        personal_df.to_excel(writer, sheet_name='Account Info', index=False)
        summary_df.to_excel(writer, sheet_name='Monthly Summary', index=False)
        analytics_df.to_excel(writer, sheet_name='Analytics', index=False)
        trx_df.to_excel(writer, sheet_name='Transactions', index=False)
        partner_trx_df.to_excel(writer, sheet_name='Partner Summary', index=False)
    print(f"✓ Excel file saved to: {output_path}")

def get_cell(df, col, default="Unknown"):
    """Safely get cell value from dataframe"""
    try:
        val = df.at[0, col]
        return val if (val is not None and str(val).strip() != "") else default
    except Exception:
        return default

def print_summary(personal_df, summary_df, trx_df, partner_trx_df, analytics_df):
    """Print analysis summary to console"""
    print("\n" + "="*60)
    print("BCA STATEMENT ANALYSIS SUMMARY")
    print("="*60)

    # Account Info
    print("\n[ACCOUNT INFORMATION]")
    print(f"  Bank: {get_cell(personal_df, 'Bank', 'N/A')}")
    print(f"  Account Name: {get_cell(personal_df, 'Account Name', 'N/A')}")
    print(f"  Account Number: {get_cell(personal_df, 'Account Number', 'N/A')}")
    print(f"  Branch: {get_cell(personal_df, 'Branch', 'N/A')}")
    print(f"  Period: {get_cell(personal_df, 'Period', 'N/A')}")
    print(f"  Currency: {get_cell(personal_df, 'Currency', 'N/A')}")

    # Financial Summary
    if not summary_df.empty:
        print("\n[FINANCIAL SUMMARY]")
        for col in summary_df.columns:
            val = get_cell(summary_df, col, 'N/A')
            if isinstance(val, (int, float)) and val != 'N/A':
                print(f"  {col}: {val:,.2f}")
            else:
                print(f"  {col}: {val}")

    # Transaction Stats
    print(f"\n[TRANSACTION STATISTICS]")
    print(f"  Total Transactions: {len(trx_df)}")

    if not analytics_df.empty:
        print(f"  Credit Transactions: {get_cell(analytics_df, 'No_of_Credit', 0)}")
        print(f"  Debit Transactions: {get_cell(analytics_df, 'No_of_Debit', 0)}")
        print(f"  Total Credit Amount: {get_cell(analytics_df, 'Total_Credit_Amount', 0):,.2f}")
        print(f"  Total Debit Amount: {get_cell(analytics_df, 'Total_Debit_Amount', 0):,.2f}")
        print(f"  Unique Partners: {get_cell(analytics_df, 'Total_Partners', 0)}")

        top_partner = get_cell(analytics_df, 'Top_Partner', 'N/A')
        if top_partner != 'N/A':
            top_amount = get_cell(analytics_df, 'Top_Partner_Amount', 0)
            print(f"  Top Partner: {top_partner} ({top_amount:,.2f})")

    # Partner Summary Preview
    if not partner_trx_df.empty:
        print(f"\n[TOP 5 PARTNERS BY VOLUME]")
        top_5 = partner_trx_df.head(5)
        for idx, row in top_5.iterrows():
            total_vol = row['Total_Credit'] + row['Total_Debit']
            print(f"  {idx+1}. {row['Partner']}: {total_vol:,.2f} ({row['Total_Transactions']} txns)")

    print("\n" + "="*60 + "\n")

def main():
    parser = argparse.ArgumentParser(
        description='BCA E-Statement Analyzer - Standalone CLI Version',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python bca_analyzer_local.py statement.pdf
  python bca_analyzer_local.py statement.pdf -o output.xlsx
  python bca_analyzer_local.py statement.pdf --output-dir ./reports/
        """
    )

    parser.add_argument(
        'pdf_file',
        type=str,
        help='Path to the BCA PDF statement file'
    )

    parser.add_argument(
        '-o', '--output',
        type=str,
        default=None,
        help='Output Excel file path (default: auto-generated based on statement info)'
    )

    parser.add_argument(
        '--output-dir',
        type=str,
        default=None,
        help='Output directory for the Excel file (default: same as input PDF)'
    )

    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Suppress console output (only show errors)'
    )

    args = parser.parse_args()

    # Validate input file
    pdf_path = Path(args.pdf_file)
    if not pdf_path.exists():
        print(f"Error: File not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    if not pdf_path.is_file():
        print(f"Error: Not a file: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    if pdf_path.suffix.lower() != '.pdf':
        print(f"Warning: File does not have .pdf extension: {pdf_path}", file=sys.stderr)

    # Process the PDF
    if not args.quiet:
        print(f"Processing: {pdf_path}")
        print("Reading PDF and extracting data...")

    try:
        # Read and parse
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()

        personal_df, summary_df, trx_df, partner_trx_df, analytics_df = parse_bca_statement(
            io.BytesIO(pdf_bytes)
        )

        if not args.quiet:
            print(f"✓ Extracted {len(trx_df)} transactions")

        # Determine output path
        if args.output:
            output_path = Path(args.output)
        else:
            # Auto-generate filename
            period = get_cell(personal_df, "Period", "Unknown")
            account_no = get_cell(personal_df, "Account Number", "XXXX")

            base_name = f"BCA_Statement_Analysis_{period}_{account_no}".strip("_")
            filename = safe_filename(base_name) + ".xlsx"

            if args.output_dir:
                output_path = Path(args.output_dir) / filename
                output_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                output_path = pdf_path.parent / filename

        # Create Excel file
        create_excel_file(personal_df, summary_df, trx_df, partner_trx_df, analytics_df, output_path)

        # Print summary
        if not args.quiet:
            print_summary(personal_df, summary_df, trx_df, partner_trx_df, analytics_df)

        print(f"\n✓ Analysis complete! Output saved to: {output_path.absolute()}")

    except Exception as e:
        print(f"\nError processing PDF: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
