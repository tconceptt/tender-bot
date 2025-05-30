import time
from urllib.parse import urljoin
import os
import json
import telegram

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# --- Configuration ---
BASE_URL = 'https://tender.2merkato.com/tenders'
CATEGORIES = '61bbe243cfb36d443e8959ff'
POLITENESS_DELAY_SECONDS = 2
WEBDRIVER_WAIT_TIMEOUT = 20
MAX_PAGES_TO_SCRAPE = 10
MAX_INITIAL_SEND = 10

# --- Telegram and State Management Configuration ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHAT_IDS_FILE = 'chat_ids.json'
SENT_TENDERS_FILE = 'sent_tenders.json'

# --- Helper Functions ---
def clean_text(text_obj):
    if text_obj: return str(text_obj).strip().replace('\n', ' ').replace('\r', ' ')
    return 'N/A'

def load_sent_tenders(filepath):
    is_first_run = not os.path.exists(filepath)
    if is_first_run: return set(), True
    try:
        with open(filepath, 'r') as f:
            return set(json.load(f)), False
    except (json.JSONDecodeError, IOError):
        return set(), True

def save_sent_tenders(filepath, tender_ids):
    with open(filepath, 'w') as f:
        json.dump(list(tender_ids), f, indent=4)
        print(f"✅ Successfully saved {len(tender_ids)} tender IDs to {filepath}")

def send_telegram_message(bot, chat_id, tender, is_initial=False):
    initial_text = "*(Initial Setup)* " if is_initial else ""
    message = (
        f"📢 {initial_text}*New Tender Found!*\n\n"
        f"📄 *Title:* {tender['Title']}\n"
        f"🏢 *Purchaser:* {tender['Purchaser']}\n"
        f"📅 *Closing Date:* {tender['Closing Date']}\n"
        f"📍 *Location:* {tender['Location']}\n"
        f"🔗 *Details:* [View Tender]({tender['Detail Page URL']})"
    )
    try:
        bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
        print(f"✅ Successfully sent notification for '{tender['Title']}' to chat ID {chat_id}")
    except telegram.error.TelegramError as e:
        print(f"❌ Error sending to chat ID {chat_id}: {e}")

def parse_tenders_from_json_data(json_data_str, base_site_url):
    all_tenders_details = []
    try:
        data = json.loads(json_data_str)
        tender_items = data.get("props", {}).get("tenders", {}).get("data", [])
        for item in tender_items:
            if item.get("is_open") is True:
                tender_id = item.get("id")
                if not tender_id: continue
                purchaser_obj = item.get("company", {}) or {}
                region_obj = item.get("region", {}) or {}
                all_tenders_details.append({
                    'Title': clean_text(item.get("title")),
                    'Purchaser': clean_text(purchaser_obj.get("name_en", "N/A")),
                    'Closing Date': clean_text(item.get("bid_closing_date_text")),
                    'Location': clean_text(region_obj.get("name_en", "N/A")),
                    'Detail Page URL': urljoin(base_site_url, f"/tenders/{tender_id}"),
                    'Tender ID': tender_id
                })
    except (json.JSONDecodeError, Exception) as e:
        print(f"Error parsing tender data: {e}")
    return all_tenders_details

# --- Main Logic ---
def check_for_new_tenders():
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN secret is not set.")
        return

    try:
        with open(CHAT_IDS_FILE, 'r') as f:
            chat_ids = json.load(f)
        if not isinstance(chat_ids, list) or not chat_ids:
            print("ERROR: chat_ids.json is empty or not a list.")
            return
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"ERROR: Could not find or read {CHAT_IDS_FILE}.")
        return

    print(f"\n--- [{time.strftime('%Y-%m-%d %H:%M:%S')}] Running tender check for {len(chat_ids)} chats... ---")
    sent_tender_ids, is_first_run = load_sent_tenders(SENT_TENDERS_FILE)
    if is_first_run:
        print(f"💡 First run detected. Will perform a deep scrape to build a baseline.")
    else:
        print(f"📚 Loaded {len(sent_tender_ids)} previously known tender IDs. Starting efficient check.")

    bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    driver = None
    
    new_tenders_found_this_run = 0
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        initial_sent_count = 0

        for page_num in range(1, MAX_PAGES_TO_SCRAPE + 1):
            print(f"Navigating to page {page_num}...")
            driver.get(f"{BASE_URL}?{'&'.join([f'{k}={v}' for k, v in {'categories': CATEGORIES, 'page': page_num}.items()])}")

            try:
                app_div = WebDriverWait(driver, WEBDRIVER_WAIT_TIMEOUT).until(EC.presence_of_element_located((By.ID, "app")))
                data_page_json_str = app_div.get_attribute("data-page")
                if not data_page_json_str: break
                
                tenders_on_page = parse_tenders_from_json_data(data_page_json_str, 'https://tender.2merkato.com')
                if not tenders_on_page: 
                    print("No tenders found on this page. Stopping.")
                    break
                
                newly_found_on_this_page = 0
                total_on_this_page = len(tenders_on_page)

                for tender in tenders_on_page:
                    tender_id = tender['Tender ID']
                    if tender_id not in sent_tender_ids:
                        newly_found_on_this_page += 1
                        new_tenders_found_this_run += 1
                        sent_tender_ids.add(tender_id)
                        
                        if is_first_run:
                            if initial_sent_count < MAX_INITIAL_SEND:
                                print(f"✨ INITIAL SEND: '{tender['Title']}'")
                                for chat_id in chat_ids:
                                    send_telegram_message(bot, chat_id, tender, is_initial=True)
                                initial_sent_count += 1
                            else:
                                print(f"🔍 PRIMING: Found tender '{tender['Title']}'")
                        else:
                            print(f"✨ NEW TENDER: '{tender['Title']}'")
                            for chat_id in chat_ids:
                                send_telegram_message(bot, chat_id, tender)
                
                if not is_first_run and newly_found_on_this_page < total_on_this_page:
                    print(f"Stopping scrape because Page {page_num} contained previously seen tenders.")
                    break
            except TimeoutException:
                print(f"Page {page_num} did not load correctly.")
                break
            time.sleep(POLITENESS_DELAY_SECONDS)

        if new_tenders_found_this_run > 0:
            save_sent_tenders(SENT_TENDERS_FILE, sent_tender_ids)
        else:
            print("No new tenders found in this run.")

        if is_first_run:
            print(f"\nInitial setup complete. Sent {initial_sent_count} and primed a total of {new_tenders_found_this_run} tenders.")
        else:
            print(f"\nFinished efficient run. Found and sent {new_tenders_found_this_run} new tender(s).")
            
    except Exception as e:
        print(f"A critical error occurred: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if driver: driver.quit()
        print("--- Tender check complete. ---")

if __name__ == '__main__':
    check_for_new_tenders()