# bri_analyzer_local.py
import re
import io
import json
import os
from pathlib import Path
from typing import Dict, List
from datetime import datetime
import argparse
import sys

import pandas as pd
import pdfplumber

# ============== General Helpers ==============

def read_pdf_to_text(pdf_src):
    text = ""
    try:
        if isinstance(pdf_src, (bytes, bytearray)):
            fobj = io.BytesIO(pdf_src)
        elif hasattr(pdf_src, "read"):  # BytesIO / file-like
            fobj = pdf_src
        else:
            fobj = pdf_src  # path string/Path

        with pdfplumber.open(fobj) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        print(f"Error reading PDF: {e}")
    return text

def extract_filename_id(filename: str) -> str:
    if '/' in filename or '\\' in filename:
        filename = Path(filename).name
    return os.path.splitext(filename)[0]

def clean_amount(amount_str: str) -> float:
    if not amount_str or str(amount_str).strip() == '':
        return 0.0
    try:
        cleaned = re.sub(r'[,\s]', '', str(amount_str))
        # Jika ada titik yang bukan desimal, hilangkan
        if '.' in cleaned:
            parts = cleaned.split('.')
            if not (len(parts) == 2 and len(parts[1]) == 2):
                cleaned = cleaned.replace('.', '')
        return float(cleaned)
    except:
        return 0.0

def safe_filename(text: str, default: str = "BRI_Statement_Analysis") -> str:
    if not text or str(text).strip().lower() in {"none", "nan", "nat"}:
        text = default
    text = str(text)
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    text = re.sub(r"\s+", "_", text).strip("_")
    text = re.sub(r"[^A-Za-z0-9._-]", "", text)
    return text[:120] if len(text) > 120 else text

def detect_format_by_filename(filename: str) -> str:
    """Deteksi format berdasarkan nama file saja."""
    base = Path(filename).name.lower()

    cms_keys = [
        "2024", "cms"
    ]
    est_keys = [
        "2025", "e-statement"
    ]

    if any(k in base for k in cms_keys):
        return "CMS"
    if any(k in base for k in est_keys):
        return "E_STATEMENT"

    # Heuristik tahun (kalau penamaan kamu konsisten)
    if re.search(r"\b2025\b", base):
        return "E_STATEMENT"
    if re.search(r"\b2024\b", base):
        return "CMS"

    # Default jika tak terdeteksi
    return "E_STATEMENT"

# ============== BRI 2024 Format ==============

def extract_cms_account_info(text):
    account_info = {
        "Bank": "BRI",
        "Account Name": None,
        "Account Number": None,
        "Start Period": None,
        "End Period": None,
    }

    # Account No
    account_patterns = [
        r'Account\s+No\s*:?\s*(\d{4}-\d{2}-\d{6}-\d{2}-\d)',
        r'Account\s+No\s*:?\s*(\d+)',
        r'Account\s+No\s*\n*\s*:?\s*(\d{4}-\d{2}-\d{6}-\d{2}-\d)',
        r'Account\s+No\s*\n*\s*(\d+)',
    ]
    for p in account_patterns:
        m = re.search(p, text, re.IGNORECASE | re.DOTALL)
        if m:
            account_info["Account Number"] = m.group(1).strip()
            break

    # Account Name
    name_patterns = [
        r'Account\s+Name\s*:?\s*([A-Z][A-Z\s&\.]+?)(?=\s*Today\s*Hold|\s*Period|\s*Account\s*Status)',
        r'Account\s+Name\s*\n*\s*:?\s*([A-Z][A-Z\s&\.]+?)(?=\s*Today|\s*Period|\s*Account\s*Status)',
        r'Account\s+Name\s+([A-Z][A-Z\s&\.]+?)(?=\s*Today|\s*Period|\s*Account\s*Status)',
        r'Account\s+Name\s*:?\s*(PT\s+[A-Z\s]+)',
        r'Account\s+Name\s*:?\s*([A-Z][A-Z\s&\.PT]+)',
        r'Account\s+Name\s*:?\s*([A-Z\s&\.PT]+?)(?=\s*\n|\s*Today|\s*Period|\s*Account)',
    ]
    for p in name_patterns:
        m = re.search(p, text, re.IGNORECASE | re.DOTALL)
        if m:
            account_info["Account Name"] = m.group(1).strip()
            break

    # Period
    period_patterns = [
        r'Period\s*:?\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})',
        r'Period\s*\n*\s*:?\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})',
    ]
    for p in period_patterns:
        m = re.search(p, text, re.IGNORECASE | re.DOTALL)
        if m:
            account_info['Start Period'] = m.group(1)
            account_info['End Period'] = m.group(2)
            break

    # Fallback berbasis baris
    if not account_info.get("Account Name") or not account_info.get("Account Number"):
        lines = text.split('\n')
        for i, line in enumerate(lines):
            s = line.strip()
            if 'Account No' in s and ':' in s:
                parts = s.split(':', 1)
                account_info['Account Number'] = parts[1].strip()
            elif 'Account Name' in s and ':' in s:
                parts = s.split(':', 1)
                account_info['Account Name'] = parts[1].strip()
            elif 'Period' in s and ':' in s:
                parts = s.split(':', 1)
                m = re.search(r'(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})', parts[1])
                if m:
                    account_info['Start Period'] = m.group(1)
                    account_info['End Period'] = m.group(2)
    return account_info

def extract_cms_transactions(text):
    transactions = []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        if re.match(r'^\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}', line):
            try:
                parts = line.split()
                if len(parts) >= 6:
                    # ambil 4 elemen numeric terakhir (debit, credit, saldo, tellerid—urutan bisa bervariasi)
                    numeric_parts = []
                    for i in range(len(parts) - 1, -1, -1):
                        p = parts[i]
                        if re.match(r'^[\d,\.]+$', p) or re.match(r'^\d{7}$', p) or p in ['CMSPYRL','BRI0372','BRIMDBT']:
                            numeric_parts.insert(0, p)
                            if len(numeric_parts) == 4:
                                break
                    if len(numeric_parts) >= 4:
                        debet_str, credit_str, ledger_str, teller_id = numeric_parts[0], numeric_parts[1], numeric_parts[2], numeric_parts[3]
                        debet  = clean_amount(debet_str) if debet_str != '0.00' else 0.0
                        credit = clean_amount(credit_str) if credit_str != '0.00' else 0.0
                        ledger = clean_amount(ledger_str)

                        start_idx = 2
                        end_idx   = len(parts) - 4
                        remark = ' '.join(parts[start_idx:end_idx]).strip() if end_idx > start_idx else ""

                        transactions.append({
                            'Date': parts[0],
                            'Remark': remark,
                            'Debit': debet,
                            'Credit': credit,
                            'Saldo': ledger
                        })
            except:
                continue
    return transactions

def extract_cms_summary(text):
    summary = {}
    pat = re.compile(
        r'OPENING\s+BALANCE\s+TOTAL\s+DEBET\s+TOTAL\s+CREDIT\s+CLOSING\s+BALANCE\s*\n'
        r'\s*([\d,\.]+)\s+([\d,\.]+)\s+([\d,\.]+)\s+([\d,\.]+)',
        re.IGNORECASE | re.MULTILINE
    )
    m = pat.search(text)
    if m:
        try:
            summary['Saldo Awal']     = clean_amount(m.group(1))
            summary['Mutasi Debit']   = clean_amount(m.group(2))
            summary['Mutasi Credit']  = clean_amount(m.group(3))
            summary['Saldo Akhir']    = clean_amount(m.group(4))
        except:
            pass
    return summary

# ============== BRI E-Statement (umum 2025) ==============

def extract_personal_info(text):
    personal_info = {
        "Bank": "BRI",
        "Account Name": None,
        "Account Number": None,
        "Address": None,
        "Report Date": None,
        "Branch": None,
        "Business Unit Address": None,
        "Product Name": None,
        "Currency": None,
        "Period": None,
        "Start Period": None,
        "End Period": None
    }

    nama_patterns = [
        r'(?:Kepada\s+Yth\.\s*/\s*To\s*:\s*\n\s*[A-Z][A-Z\s]*?\n)\s*(.*?)(?=\n\n|\nNo\.\s*Rekening|\nTanggal\s+Laporan|\nPeriode\s+Transaksi|\nNo\s+Rekening)',
        r'Kepada\s+Yth\.\s*/\s*To\s*:\s*\n\s*([^\n]+)(?:\n([^\n]*?))*?(?=\n\s*No\.\s*Rekening|\n\s*Tanggal\s+Laporan|\n\s*Account\s+No)',
        r'Kepada\s+Yth\.\s*/\s*To\s*:\s*\n\s*(.+?)(?=\n\s*No\.\s*Rekening)',
    ]

    for pattern in nama_patterns:
        nama_match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if nama_match:
            extracted_text = nama_match.group(1).strip()
            lines = [line.strip() for line in extracted_text.split('\n') if line.strip()]

            if lines:
                # personal_info['Account Name'] = lines[0]
                nama_clean = lines[0]
                nama_clean = re.sub(r'\s+Periode\s+Transaksi.*', '', nama_clean, flags=re.IGNORECASE).strip()
                personal_info['Account Name'] = nama_clean

                if len(lines) > 1:
                    alamat_lines = lines[1:]
                    alamat_filtered = []
                    for line in alamat_lines:
                      if not re.match(r'\d{2}/\d{2}/\d{2,4}', line) and 'Periode Transaksi' not in line:
                        alamat_filtered.append(line)
                    if alamat_filtered:
                      alamat_valid = []
                      for line in alamat_filtered:
                        if 'Transaction Period' not in line:
                          alamat_valid.append(line)

                      if alamat_valid:
                          alamat_cleaned = ' '.join(alamat_valid)
                          alamat_cleaned = re.sub(r'\s+', ' ', alamat_cleaned)
                          personal_info['Address'] = alamat_cleaned
            break

    if 'Account Name' not in personal_info:
        simple_nama_pattern = r'Kepada\s+Yth\.\s*/\s*To\s*:\s*\n\s*([A-Z][A-Z\s]+)'
        simple_match = re.search(simple_nama_pattern, text, re.IGNORECASE)
        if simple_match:
            personal_info['nama'] = simple_match.group(1).strip()

    tanggal_patterns = [
        r'Tanggal\s+Laporan\s*[:\s]*(\d{2}/\d{2}/\d{2,4})',
        r'Statement\s+Date\s*[:\s]*(\d{2}/\d{2}/\d{2,4})',
    ]
    # Tanggal laporan
    for pattern in tanggal_patterns:
        tanggal_match = re.search(pattern, text, re.IGNORECASE)
        if tanggal_match:
            personal_info['Report Date'] = tanggal_match.group(1)
            break

    # Ekstrak periode transaksi dengan error handling
    periode_patterns = [
        r'Periode\s+Transaksi\s*[:\s]*(\d{2}/\d{2}/\d{2,4})\s*-\s*(\d{2}/\d{2}/\d{2,4})',
        r'Transaction\s+Period\s*[:\s]*(\d{2}/\d{2}/\d{2,4})\s*-\s*(\d{2}/\d{2}/\d{2,4})',
    ]
    for pattern in periode_patterns:
        periode_match = re.search(pattern, text, re.IGNORECASE)
        if periode_match:
            personal_info['Start Period'] = periode_match.group(1)
            personal_info['End Period'] = periode_match.group(2)
            break

    # Ekstrak nomor rekening dengan error handling
    rekening_patterns = [
        r'No\.\s*Rekening\s*\n*Account\s*No\s*[,:]*\s*(\d+)',
        r'No\.\s*Rekening\s*[:\s]*(\d+)',
        r'Account\s*No\s*[:\s]*(\d+)',
    ]
    for pattern in rekening_patterns:
        rekening_match = re.search(pattern, text, re.IGNORECASE)
        if rekening_match:
            personal_info['Account Number'] = rekening_match.group(1)
            break

    # Ekstrak nama produk dengan error handling
    produk_patterns = [
        r'(?:Nama\s+Produk|Product\s+Name)\s*[,:]*\s*(.*?)(?=\s*(?:Unit\s*Kerja|Business\s*Unit|Valuta|Currency|Alamat\s*Unit\s*Kerja|\n|$))',
    ]
    for pattern in produk_patterns:
        produk_match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if produk_match:
            personal_info['Product Name'] = produk_match.group(1).strip()
            break

    # Ekstrak valuta dengan error handling
    valuta_patterns = [
        r'Valuta\s*\n*Currency\s*[,:]*\s*([A-Z]+)',
        r'Valuta\s*[:\s]*([A-Z]+)',
        r'Currency\s*[:\s]*([A-Z]+)',
    ]
    for pattern in valuta_patterns:
        valuta_match = re.search(pattern, text, re.IGNORECASE)
        if valuta_match:
            personal_info['Currency'] = valuta_match.group(1).strip()
            break

    # Ekstrak unit kerja dengan error handling
    unit_patterns = [
        r'Unit\s+Kerja\s*\n*Business\s+Unit\s*[,:]*\s*([A-Z][A-Z\s]*?)(?=\s*(?:Alamat\s+Unit|Business\s+Unit\s+Address|\n|$))',
        r'Unit\s+Kerja\s*[:\s]*([A-Z][A-Z\s]*?)(?=\s*(?:Alamat\s+Unit|Business\s+Unit\s+Address|\n|$))',
        r'Business\s+Unit\s*[:\s]*([A-Z][A-Z\s]*?)(?=\s*(?:Alamat\s+Unit|Business\s+Unit\s+Address|\n|$))',
    ]
    for pattern in unit_patterns:
        unit_match = re.search(pattern, text, re.IGNORECASE)
        if unit_match:
            personal_info['Branch'] = unit_match.group(1).strip()
            break

    # Ekstrak alamat unit kerja dengan error handling
    alamat_unit_kerja_patterns = [
        r'(?:Alamat\s+Unit\s+Kerja|Business\s+Unit\s+Address)\s*[,:]*\s*\n\s*([A-Z][A-Z\s]*?)\n\s*([A-Z][A-Z\s]*?)(?=\n|$)',
        r'(?:Alamat\s+Unit\s+Kerja|Business\s+Unit\s+Address)\s*[,:]*\s*([A-Z][A-Z\s]*?)\n\s*([A-Z][A-Z\s]*?)(?=\n|$)',
        r'(?:Alamat\s+Unit\s+Kerja|Business\s+Unit\s+Address)\s*[,:]*\s*([A-Z][A-Z\s]*?)(?=\n|$)',
    ]

    for pattern in alamat_unit_kerja_patterns:
        alamat_unit_match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if alamat_unit_match:
            if len(alamat_unit_match.groups()) >= 2:
                alamat_temp = f"{alamat_unit_match.group(1).strip()} {alamat_unit_match.group(2).strip()}"
            else:
                alamat_temp = alamat_unit_match.group(1).strip()

            alamat_temp = re.sub(r'Product\s+Name\s*Business\s+Unit\s*Address ', '', alamat_temp, flags=re.IGNORECASE).strip()
            personal_info['Business Unit Address'] = alamat_temp
            break

    # --- Ekstraksi Informasi Finansial (Saldo) dengan error handling ---
    financial_summary = {}

    try:
        balance_summary_pattern = re.compile(
            r'(?:Saldo Awal|Opening Balance)\s*\n?'
            r'(?:Opening Balance)?\s*\n?'
            r'(?:Total Transaksi Debet|Total Debit Transaction)\s*\n?'
            r'(?:Total Debit Transaction)?\s*\n?'
            r'(?:Total Transaksi Kredit|Total Credit Transaction)\s*\n?'
            r'(?:Total Credit Transaction)?\s*\n?'
            r'(?:Saldo Akhir|Closing Balance)\s*\n?'
            r'(?:Closing Balance)?\s*\n?'
            r'([\d,\.]+\s+[\d,\.]+\s+[\d,\.]+\s+[\d,\.]+)',
            re.IGNORECASE | re.DOTALL
        )

        financial_match = balance_summary_pattern.search(text)
        if financial_match:
            amounts_line = financial_match.group(1).strip()
            amounts = amounts_line.split()

            def parse_amount(amount_str):
                try:
                    amount_str = amount_str.strip()
                    if ',' in amount_str and amount_str.rfind(',') > amount_str.rfind('.'):
                        amount_str = amount_str.replace('.', '')
                        amount_str = amount_str.replace(',', '.')
                    elif ',' in amount_str:
                        amount_str = amount_str.replace(',', '')
                    elif amount_str.count('.') > 1:
                        parts = amount_str.rsplit('.', 1)
                        integer_part = parts[0].replace('.', '')
                        if len(parts) > 1:
                            decimal_part = parts[1]
                            amount_str = f"{integer_part}.{decimal_part}"
                        else:
                            amount_str = integer_part
                    return float(amount_str)
                except:
                    return 0.0

            if len(amounts) >= 4:
                financial_summary['opening_balance'] = parse_amount(amounts[0])
                financial_summary['total_debit_transaction'] = parse_amount(amounts[1])
                financial_summary['total_credit_transaction'] = parse_amount(amounts[2])
                financial_summary['closing_balance'] = parse_amount(amounts[3])

    except Exception as e:
        print(f"Error extracting financial summary: {e}")

    return personal_info, financial_summary

def extract_transactions(text: str) -> List[Dict]:
    """Extract semua transaksi dari bank statement"""
    transactions = []

    # Pattern untuk menangkap transaksi
    # Format: DD/MM/YY HH:MM:SS Description TellerID Debit Credit Balance
    transaction_pattern = r'(\d{2}/\d{2}/\d{2})\s+(\d{2}:\d{2}:\d{2})\s+(.+?)\s+(\d{7})\s+([\d,\.]+)\s+([\d,\.]+)\s+([\d,\.]+)'

    lines = text.split('\n')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Cek apakah baris dimulai dengan tanggal
        if re.match(r'^\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}', line):
            # Split berdasarkan spasi, tapi hati-hati dengan deskripsi yang panjang
            parts = line.split()
            if len(parts) >= 7:
                try:
                    date = parts[0]
                    time = parts[1]
                    teller_id = None
                    debit = None
                    credit = None
                    balance = None

                    # Cari teller ID (7 digit number), debit, credit, balance dari akhir
                    # Ambil 4 elemen terakhir
                    numeric_parts = []
                    for i in range(len(parts) - 1, -1, -1):
                        if re.match(r'^[\d,\.]+$', parts[i]) or re.match(r'^\d{7}$', parts[i]):
                            numeric_parts.insert(0, parts[i])
                            if len(numeric_parts) == 4:
                                break

                    if len(numeric_parts) == 4:
                        teller_id = numeric_parts[0]
                        debit = clean_amount(numeric_parts[1])
                        credit = clean_amount(numeric_parts[2])
                        balance = clean_amount(numeric_parts[3])

                        # Description adalah sisa parts setelah date, time dan sebelum 4 numeric parts terakhir
                        desc_start = 2  # setelah date dan time
                        desc_end = len(parts) - 4  # sebelum 4 numeric parts
                        description = ' '.join(parts[desc_start:desc_end])

                        transaction = {
                            'tanggal': date,
                            'waktu': time,
                            'deskripsi': description.strip(),
                            'teller_id': teller_id,
                            'debit': debit,
                            'kredit': credit,
                            'saldo': balance
                        }

                        transactions.append(transaction)

                except (ValueError, IndexError) as e:
                    # Skip baris yang tidak bisa diparse
                    continue

    return transactions

# ============== Partner Extraction & Analytics ==============

def extract_partner_name_bri(description: str):
    if not description or (isinstance(description, float) and pd.isna(description)):
        return None

    skip_keywords = [
        "BIAYA", "ADM", "BUNGA", "PAJAK", "KLIRING", "TARIK TUNAI",
        "SETORAN", "BI-FAST", "BPJS", "TAX", "INTEREST",
        "FEE", "SINGLE CN", "POLLING", "REWARD", "CLAIM", "BPJS TK", "BPJS KESEHATAN"
    ]
    if any(k in str(description).upper() for k in skip_keywords):
        return None

    cleaned = str(description).strip()

    # Pola umum TRF/TRANSFER KE/TO
    m = re.search(r'(?:TRF|TRANSFER)\s+(?:KE|TO)\s+([A-Z\s\.\-&]{3,})', cleaned.upper())
    if m:
        name = re.sub(r'\s{2,}', ' ', m.group(1).title().strip())
        if len(name) >= 3:
            return name

    # BM
    if re.search(r'BM\d+', cleaned):
        lines = [ln.strip() for ln in cleaned.split('\n') if ln.strip()]
        words = []
        esb_found = False
        for ln in lines:
            if ln.startswith('ESB:'):
                esb_found = True
                continue
            if re.match(r'^BM\d+\s+\d+\s+\d+', ln):
                mm = re.search(r'BM\d+\s+\d+\s+\d+\s+(.+)', ln)
                if mm:
                    words += [w for w in mm.group(1).split() if w.isalpha() and len(w) >= 2]
            else:
                if not esb_found:
                    words += [w for w in ln.split() if w.isalpha() and len(w) >= 2]
        if words:
            return ' '.join(words)

    # NBMB
    if "NBMB" in cleaned.upper():
        mm = re.search(r'NBMB\s+(.*?)\s+TO\s+(.*?)(?:\n|ESB:|$)', cleaned, re.IGNORECASE | re.DOTALL)
        if mm:
            receiver = mm.group(2).strip()
            sender   = mm.group(1).strip()
            return receiver if receiver else sender

    # WBNKTRF
    if "WBNKTRF" in cleaned.upper():
        after = re.sub(r'WBNKTRF\w+', '', cleaned, flags=re.IGNORECASE)
        after = re.sub(r'ESB:.*', '', after, flags=re.DOTALL)
        words = [w for w in after.split() if w.isalpha() and len(w) >= 2]
        if words:
            return ' '.join(words)

    # BFST
    if "BFST" in cleaned.upper():
        cnt = re.sub(r'BFST\d+', '', cleaned, flags=re.IGNORECASE)
        cnt = re.sub(r'ESB:.*', '', cnt, flags=re.DOTALL)
        cnt = re.sub(r'\d{8,}', '', cnt)
        if ':' in cnt:
            for part in cnt.split(':'):
                nm = ''.join(c for c in part if c.isalpha() or c.isspace()).strip()
                if nm and len(nm) >= 3:
                    return nm
        else:
            words = [w for w in cnt.split() if w.isalpha() and len(w) >= 2]
            if words:
                return ' '.join(words)

    # IBIZ
    if "IBIZ" in cleaned.upper() and " TO " in cleaned.upper():
        parts = cleaned.split(" TO ")
        if len(parts) > 1:
            receiver_part = parts[1].split("ESB:")[0].strip()
            words = [w for w in receiver_part.split() if w.isalpha() and len(w) >= 2]
            if words:
                return ' '.join(words[:4])

    # Payroll
    if "PAYROLL" in cleaned.upper():
        return "PAYROLL"

    # Setoran penjualan
    if "SETOR" in cleaned.upper() and "PENJUALAN" in cleaned.upper():
        return "PENJUALAN INTERNAL"

    # --- Fallback umum: ambil kandidat nama dari teks yang dibersihkan ---
    cleaned_up = re.sub(r'ESB:.*', ' ', cleaned, flags=re.DOTALL)
    cleaned_up = re.sub(r'\b[A-Z]{2,}\d+[A-Z]*\b', ' ', cleaned_up)   # kode seperti BFST123
    cleaned_up = re.sub(r'\b\d{5,}\b', ' ', cleaned_up)               # angka panjang
    cleaned_up = re.sub(r'[^A-Za-z\s&\.-]', ' ', cleaned_up)
    cleaned_up = re.sub(r'\s+', ' ', cleaned_up).strip()

    stop = {"TRANSFER","TRF","KE","TO","BAYAR","PEMBAYARAN","PENERIMA","PENGIRIM",
            "VIA","BANK","BRI","PT","CV","QRIS","BRIVA","VA","BIFAST","BI","FAST",
            "RTGS","LLG","KLIRING","ADM","PAJAK","BUNGA","FEE","SETOR","SETORAN"}
    words = [w for w in cleaned_up.upper().split() if w not in stop and len(w) >= 3]
    if words:
        cand = ' '.join(words[:6]).title()
        if len(cand) >= 3:
            return cand

    return None

def detect_bri_format(transactions_df):
    if 'Remark' in transactions_df.columns: return 'CMS'
    if 'deskripsi' in transactions_df.columns: return 'E_STATEMENT'
    return 'UNKNOWN'

def analyze_bri_partners_unified(transactions_df):
    if transactions_df.empty:
        return transactions_df, pd.DataFrame()

    fmt = detect_bri_format(transactions_df)
    df = transactions_df.copy()

    if fmt == 'CMS':
        desc_col, debit_col, credit_col = 'Remark', 'Debit', 'Credit'
    elif fmt == 'E_STATEMENT':
        desc_col, debit_col, credit_col = 'deskripsi', 'debit', 'kredit'
    else:
        return df, pd.DataFrame()

    df['partner_name'] = df[desc_col].apply(extract_partner_name_bri)
    df['transaction_type'] = df.apply(
        lambda r: 'DEBIT' if r[debit_col] > 0 else ('CREDIT' if r[credit_col] > 0 else 'UNKNOWN'), axis=1
    )
    df['amount'] = df[debit_col] + df[credit_col]

    partner_transactions = df[df['partner_name'].notna()].copy()
    if partner_transactions.empty:
        return df, pd.DataFrame()

    partner_summary = partner_transactions.groupby(['partner_name', 'transaction_type']).agg({
        debit_col: 'sum',
        credit_col: 'sum',
        'amount': 'sum',
        desc_col: 'count'
    }).rename(columns={desc_col: 'transaction_count'}).reset_index().sort_values('amount', ascending=False)

    partner_summary_table = create_partner_summary_table(df)
    return partner_summary, partner_summary_table  # ringkasan per partner/tipe + tabel ringkas

def create_partner_summary_table(partner_df):
    if partner_df.empty or 'partner_name' not in partner_df.columns:
        return pd.DataFrame()
    partner_transactions = partner_df[partner_df['partner_name'].notna()].copy()
    if partner_transactions.empty:
        return pd.DataFrame()

    if {'Debit','Credit'}.issubset(partner_transactions.columns):
        debit_col, credit_col = 'Debit', 'Credit'
    elif {'debit','kredit'}.issubset(partner_transactions.columns):
        debit_col, credit_col = 'debit', 'kredit'
    else:
        return pd.DataFrame()

    rows = []
    for partner in partner_transactions['partner_name'].unique():
        p = partner_transactions[partner_transactions['partner_name'] == partner]
        rows.append({
            'Partner': partner,
            'Total_Credit': p[credit_col].sum(),
            'Total_Debit':  p[debit_col].sum(),
            'Credit_Count': int((p[credit_col] > 0).sum()),
            'Debit_Count':  int((p[debit_col]  > 0).sum()),
            'Total_Transactions': len(p)
        })
    summary_df = pd.DataFrame(rows)
    summary_df['Total_Volume'] = summary_df['Total_Credit'] + summary_df['Total_Debit']
    summary_df = summary_df.sort_values('Total_Volume', ascending=False).drop(columns=['Total_Volume']).reset_index(drop=True)
    return summary_df

def create_partner_statistics_summary(df_for_stats):
    # df_for_stats diharapkan punya kolom partner_name atau setidaknya debit/kredit
    if df_for_stats.empty:
        return pd.DataFrame()

    if 'partner_name' in df_for_stats.columns:
        partner_transactions = df_for_stats[df_for_stats['partner_name'].notna()].copy()
    else:
        partner_transactions = df_for_stats.copy()

    if partner_transactions.empty:
        return pd.DataFrame()

    if {'Debit','Credit'}.issubset(partner_transactions.columns):
        debit_col, credit_col = 'Debit', 'Credit'
    elif {'debit','kredit'}.issubset(partner_transactions.columns):
        debit_col, credit_col = 'debit', 'kredit'
    else:
        return pd.DataFrame()

    total_credit_transactions = int((partner_transactions[credit_col] > 0).sum())
    total_debit_transactions  = int((partner_transactions[debit_col]  > 0).sum())
    total_credit_amount = float(partner_transactions[credit_col].sum())
    total_debit_amount  = float(partner_transactions[debit_col].sum())
    total_unique_partners = int(partner_transactions['partner_name'].nunique()) if 'partner_name' in partner_transactions.columns else 0

    if 'partner_name' in partner_transactions.columns:
        gp = partner_transactions.groupby('partner_name').agg({debit_col:'sum', credit_col:'sum'}).reset_index()
        gp['total_volume'] = gp[debit_col] + gp[credit_col]
        top_row = gp.loc[gp['total_volume'].idxmax()] if not gp.empty else None
        top_partner_name = top_row['partner_name'] if top_row is not None else None
        top_partner_amount = float(top_row['total_volume']) if top_row is not None else 0.0
    else:
        top_partner_name, top_partner_amount = None, 0.0

    return pd.DataFrame([{
        'No_of_Credit': total_credit_transactions,
        'No_of_Debit': total_debit_transactions,
        'Total_Credit_Amount': total_credit_amount,
        'Total_Debit_Amount': total_debit_amount,
        'Total_Partners': total_unique_partners,
        'Top_Partner': top_partner_name,
        'Top_Partner_Amount': top_partner_amount
    }])

# ============== Parser Orkestrasi (autodetect) ==============

def parse_bri_statement(pdf_src, filename):
    text = read_pdf_to_text(pdf_src)
    fmt = detect_format_by_filename(filename)

    # Parsers
    if fmt == "CMS":
        personal_info = extract_cms_account_info(text)
        summary_info  = extract_cms_summary(text)
        transactions  = extract_cms_transactions(text)
        detected_format = "CMS"
    else:
        personal_info, summary_info = extract_personal_info(text)
        transactions = extract_transactions(text)
        detected_format = "E_STATEMENT"

    # (Opsional) fallback kalau benar-benar kosong → matikan kalau mau strict
    FALLBACK_IF_EMPTY = True
    if FALLBACK_IF_EMPTY and len(transactions) == 0:
        alt_fmt = "E_STATEMENT" if fmt == "CMS" else "CMS"
        try:
            if alt_fmt == "CMS":
                personal_info2 = extract_cms_account_info(text)
                summary_info2  = extract_cms_summary(text)
                transactions2  = extract_cms_transactions(text)
            else:
                personal_info2, summary_info2 = extract_personal_info(text)
                transactions2 = extract_transactions(text)
            if len(transactions2) > 0:
                personal_info, summary_info, transactions = personal_info2, summary_info2, transactions2
                detected_format = alt_fmt  # switch ke parser alternatif
        except Exception:
            pass

    trx_df = pd.DataFrame(transactions)

    partner_summary_df = pd.DataFrame()
    partner_summary_table = pd.DataFrame()
    analytics_df = pd.DataFrame()

    if not trx_df.empty:
        partner_summary_df, partner_summary_table = analyze_bri_partners_unified(trx_df)
        base_stats_df = partner_summary_df if 'partner_name' in partner_summary_df.columns else trx_df
        analytics_df = create_partner_statistics_summary(base_stats_df)

    personal_df = pd.DataFrame([personal_info])
    personal_df["Detected Format"] = detected_format
    summary_df  = pd.DataFrame([summary_info])

    return personal_df, summary_df, trx_df, partner_summary_table, analytics_df

# ============== CLI Interface ==============

def create_excel_download(personal_df, summary_df, trx_df, partner_trx_df, analytics_df, output_path):
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
    print("BRI STATEMENT ANALYSIS SUMMARY")
    print("="*60)

    # Account Info
    print("\n[ACCOUNT INFORMATION]")
    print(f"  Bank: {get_cell(personal_df, 'Bank', 'N/A')}")
    print(f"  Account Name: {get_cell(personal_df, 'Account Name', 'N/A')}")
    print(f"  Account Number: {get_cell(personal_df, 'Account Number', 'N/A')}")
    print(f"  Period: {get_cell(personal_df, 'Start Period', 'N/A')} - {get_cell(personal_df, 'End Period', 'N/A')}")
    print(f"  Detected Format: {get_cell(personal_df, 'Detected Format', 'N/A')}")

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
        description='BRI E-Statement Analyzer - Standalone CLI Version',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python bri_analyzer_local.py statement.pdf
  python bri_analyzer_local.py statement.pdf -o output.xlsx
  python bri_analyzer_local.py statement.pdf --output-dir ./reports/
        """
    )

    parser.add_argument(
        'pdf_file',
        type=str,
        help='Path to the BRI PDF statement file'
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

        personal_df, summary_df, trx_df, partner_trx_df, analytics_df = parse_bri_statement(
            pdf_bytes,
            pdf_path.name
        )

        if not args.quiet:
            print(f"✓ Detected format: {personal_df.at[0, 'Detected Format']}")
            print(f"✓ Extracted {len(trx_df)} transactions")

        # Determine output path
        if args.output:
            output_path = Path(args.output)
        else:
            # Auto-generate filename
            account_name = get_cell(personal_df, "Account Name", "UnknownName")
            account_no = get_cell(personal_df, "Account Number", "XXXX")
            report_date = get_cell(personal_df, "Report Date", "")

            base_name = f"BRI_Statement_Analysis_{account_name}_{account_no}_{report_date}".strip("_")
            filename = safe_filename(base_name) + ".xlsx"

            if args.output_dir:
                output_path = Path(args.output_dir) / filename
                output_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                output_path = pdf_path.parent / filename

        # Create Excel file
        create_excel_download(personal_df, summary_df, trx_df, partner_trx_df, analytics_df, output_path)

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
