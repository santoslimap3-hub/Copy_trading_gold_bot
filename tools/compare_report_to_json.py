#!/usr/bin/env python3
"""
Compare MT5 HTML Report with bot_trades.json
Verifies that all trades with magic 777/778 from the report are in the JSON
"""

import json
import os
import re
from pathlib import Path
from html.parser import HTMLParser

class TradeReportParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.trades = []
        self.current_row = []
        self.in_row = False
        self.in_cell = False
        
    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.in_row = True
            self.current_row = []
        elif tag == 'td' and self.in_row:
            self.in_cell = True
            
    def handle_endtag(self, tag):
        if tag == 'tr' and self.in_row:
            self.in_row = False
            if len(self.current_row) >= 3:  # Valid trade row
                self.trades.append(self.current_row)
        elif tag == 'td' and self.in_cell:
            self.in_cell = False
            
    def handle_data(self, data):
        if self.in_cell:
            self.current_row.append(data.strip())

def parse_html_report(html_file):
    """Parse MT5 HTML report and extract trades"""
    print(f"📄 Parsing HTML report: {html_file}")
    
    # Try multiple encodings
    for encoding in ['utf-16', 'utf-8-sig', 'utf-8', 'latin-1']:
        try:
            with open(html_file, 'r', encoding=encoding) as f:
                content = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        print("❌ Could not decode file with any encoding")
        return []
    
    # Extract rows from table - manual parsing since HTMLParser is tricky with complex tables
    rows = re.findall(r'<tr[^>]*>.*?</tr>', content, re.DOTALL)
    
    trades_with_magic = []
    
    for row in rows:
        # Extract cells
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        
        if len(cells) >= 14:  # Standard trade row has multiple columns
            try:
                # Clean up cell content
                cells = [re.sub(r'<[^>]+>', '', cell).strip() for cell in cells]
                
                # Try to find magic number in the row (usually in last few columns)
                row_text = ' '.join(cells)
                
                # Look for magic number pattern (777 or 778)
                if '777' in row_text or '778' in row_text:
                    magic = 777 if '777' in row_text else 778
                    
                    # Extract ticket (usually second column)
                    if len(cells) >= 2:
                        try:
                            ticket = int(cells[1])
                            trades_with_magic.append({
                                'ticket': ticket,
                                'magic': magic,
                                'row': cells
                            })
                        except (ValueError, IndexError):
                            pass
            except Exception as e:
                pass
    
    print(f"✅ Found {len(trades_with_magic)} trades with magic 777/778 in HTML report")
    return trades_with_magic

def load_json_trades(json_file):
    """Load trades from bot_trades.json"""
    print(f"📄 Loading JSON file: {json_file}")
    
    with open(json_file, 'r') as f:
        data = json.load(f)
    
    trades = data.get('trades', [])
    print(f"✅ Loaded {len(trades)} trades from JSON")
    
    return trades

def compare_trades(report_trades, json_trades):
    """Compare HTML report trades with JSON trades"""
    print("\n" + "="*60)
    print("🔍 COMPARING TRADES")
    print("="*60)
    
    # Extract ticket numbers from JSON
    json_tickets = {trade['ticket']: trade for trade in json_trades}
    report_tickets = {trade['ticket']: trade for trade in report_trades}
    
    print(f"\n📊 Summary:")
    print(f"   Report trades with magic 777/778: {len(report_tickets)}")
    print(f"   JSON trades: {len(json_tickets)}")
    
    # Find missing tickets
    missing_in_json = []
    for ticket, report_trade in report_tickets.items():
        if ticket not in json_tickets:
            missing_in_json.append((ticket, report_trade['magic']))
    
    # Find extra tickets
    extra_in_json = []
    for ticket in json_tickets:
        if ticket not in report_tickets:
            extra_in_json.append(ticket)
    
    if missing_in_json:
        print(f"\n❌ MISSING IN JSON ({len(missing_in_json)} trades):")
        for ticket, magic in sorted(missing_in_json):
            print(f"   - Ticket {ticket} (Magic {magic})")
    else:
        print(f"\n✅ All report trades are in JSON!")
    
    if extra_in_json:
        print(f"\n⚠️ EXTRA IN JSON ({len(extra_in_json)} trades):")
        for ticket in sorted(extra_in_json)[:20]:  # Show first 20
            json_trade = json_tickets[ticket]
            print(f"   - Ticket {ticket} (Magic {json_trade['magic']})")
        if len(extra_in_json) > 20:
            print(f"   ... and {len(extra_in_json) - 20} more")
    
    print("\n" + "="*60)
    
    return len(missing_in_json) == 0

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    data_dir = os.path.join(project_root, "data")
    report_file = os.path.join(data_dir, "ReportHistory-22840119.html")
    json_file = os.path.join(data_dir, "bot_trades.json")
    
    if not Path(report_file).exists():
        print(f"❌ Report file not found: {report_file}")
        return False
    
    if not Path(json_file).exists():
        print(f"❌ JSON file not found: {json_file}")
        return False
    
    # Parse HTML report
    report_trades = parse_html_report(report_file)
    
    # Load JSON
    json_trades = load_json_trades(json_file)
    
    # Compare
    all_present = compare_trades(report_trades, json_trades)
    
    return all_present

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
