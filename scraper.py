import asyncio
import aiohttp
import json
import re
import logging
from bs4 import BeautifulSoup
import os
import shutil
from datetime import datetime
import pytz
import base64
from urllib.parse import parse_qs, unquote
import aiofiles

# --- Configuration ---
URLS_FILE = 'urls.txt'
KEYWORDS_FILE = 'keywords.json' # باید حاوی کدهای دو حرفی کشور باشد
OUTPUT_DIR = 'output_configs'
COUNTRIES_DIR = 'Countries'
COUNTRIES_README_FILE = 'Countries/README.md'
README_FILE = 'README.md'
REQUEST_TIMEOUT = 15
CONCURRENT_REQUESTS = 10
MAX_CONFIG_LENGTH = 1500
MIN_PERCENT25_COUNT = 15
GITHUB_COUNTRIES_API = "https://api.github.com/repos/SoliSpirit/v2ray-configs/contents/Countries"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/SoliSpirit/v2ray-configs/main/Countries"

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# --- Protocol Categories ---
PROTOCOL_CATEGORIES = [
    "Vmess", "Vless", "Trojan", "ShadowSocks", "ShadowSocksR",
    "Tuic", "Hysteria2", "WireGuard"
]

# --- Helper function to check for Persian-like text ---
def is_persian_like(text):
    if not isinstance(text, str) or not text.strip():
        return False
    has_persian_char = False
    has_latin_char = False
    for char in text:
        if '\u0600' <= char <= '\u06FF' or char in ['\u200C', '\u200D']: # ZWNJ and ZWJ
            has_persian_char = True
        elif 'a' <= char.lower() <= 'z':
            has_latin_char = True
    return has_persian_char and not has_latin_char

# --- Base64 Decoding Helper ---
def decode_base64(data):
    try:
        data = data.replace('_', '/').replace('-', '+')
        missing_padding = len(data) % 4
        if missing_padding:
            data += '=' * (4 - missing_padding)
        return base64.b64decode(data).decode('utf-8')
    except Exception:
        return None

# --- Protocol Name Extraction Helpers ---
def get_vmess_name(vmess_link):
    if not vmess_link.startswith("vmess://"): return None
    try:
        b64_part = vmess_link[8:]
        decoded_str = decode_base64(b64_part)
        if decoded_str:
            vmess_json = json.loads(decoded_str)
            return vmess_json.get('ps')
    except Exception as e:
        logging.warning(f"Failed to parse Vmess name from {vmess_link[:30]}...: {e}")
    return None

def get_ssr_name(ssr_link):
    if not ssr_link.startswith("ssr://"): return None
    try:
        b64_part = ssr_link[6:]
        decoded_str = decode_base64(b64_part)
        if not decoded_str: return None
        parts = decoded_str.split('/?')
        if len(parts) < 2: return None
        params_str = parts[1]
        params = parse_qs(params_str)
        if 'remarks' in params and params['remarks']:
            remarks_b64 = params['remarks'][0]
            return decode_base64(remarks_b64)
    except Exception as e:
        logging.warning(f"Failed to parse SSR name from {ssr_link[:30]}...: {e}")
    return None

# --- New Filter Function ---
def should_filter_config(config):
    if 'i_love_' in config.lower(): return True
    percent25_count = config.count('%25')
    if percent25_count >= MIN_PERCENT25_COUNT: return True
    if len(config) >= MAX_CONFIG_LENGTH: return True
    if '%2525' in config: return True
    return False

async def fetch_url(session, url):
    try:
        async with session.get(url, timeout=REQUEST_TIMEOUT) as response:
            response.raise_for_status()
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            text_content = ""
            for element in soup.find_all(['pre', 'code', 'p', 'div', 'li', 'span', 'td']):
                text_content += element.get_text(separator='\n', strip=True) + "\n"
            if not text_content: text_content = soup.get_text(separator=' ', strip=True)
            logging.info(f"Successfully fetched: {url}")
            return url, text_content
    except Exception as e:
        logging.warning(f"Failed to fetch or process {url}: {e}")
        return url, None

def find_matches(text, categories_data):
    matches = {category: set() for category in categories_data}
    for category, patterns in categories_data.items():
        for pattern_str in patterns:
            if not isinstance(pattern_str, str): continue
            try:
                is_protocol_pattern = any(proto_prefix in pattern_str for proto_prefix in [p.lower() + "://" for p in PROTOCOL_CATEGORIES])
                if category in PROTOCOL_CATEGORIES or is_protocol_pattern:
                    pattern = re.compile(pattern_str, re.IGNORECASE | re.MULTILINE)
                    found = pattern.findall(text)
                    if found:
                        cleaned_found = {item.strip() for item in found if item.strip()}
                        matches[category].update(cleaned_found)
            except re.error as e:
                logging.error(f"Regex error for '{pattern_str}' in category '{category}': {e}")
    return {k: v for k, v in matches.items() if v}

def save_to_file(directory, category_name, items_set):
    if not items_set: return False, 0
    file_path = os.path.join(directory, f"{category_name}.txt")
    count = len(items_set)
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            for item in sorted(list(items_set)): f.write(f"{item}\n")
        logging.info(f"Saved {count} items to {file_path}")
        return True, count
    except Exception as e:
        logging.error(f"Failed to write file {file_path}: {e}")
        return False, 0

# --- تابع generate_simple_readme با استفاده از تصاویر پرچم ---
def generate_simple_readme(protocol_counts, country_counts, all_keywords_data, github_repo_path="miladtahanian/V2RayScrapeByCountry", github_branch="main"):
    tz = pytz.timezone('Asia/Tehran')
    now = datetime.now(tz)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S %Z")

    raw_github_base_url = f"https://raw.githubusercontent.com/{github_repo_path}/refs/heads/{github_branch}/{OUTPUT_DIR}"

    md_content = f"# 📊 نتایج استخراج (آخرین به‌روزرسانی: {timestamp})\n\n"
    md_content += "این فایل به صورت خودکار ایجاد شده است.\n\n"
    md_content += "**توضیح:** فایل‌های کشورها فقط شامل کانفیگ‌هایی هستند که نام/پرچم کشور (با رعایت مرز کلمه برای مخفف‌ها) در **اسم کانفیگ** پیدا شده باشد. اسم کانفیگ ابتدا از بخش `#` لینک و در صورت نبود، از نام داخلی (برای Vmess/SSR) استخراج می‌شود.\n\n"
    md_content += "**نکته:** کانفیگ‌هایی که به شدت URL-Encode شده‌اند (حاوی تعداد زیادی `%25`، طولانی یا دارای کلمات کلیدی خاص) از نتایج حذف شده‌اند.\n\n"

    md_content += "## 📁 فایل‌های پروتکل‌ها\n\n"
    if protocol_counts:
        md_content += "| پروتکل | تعداد کل | لینک |\n"
        md_content += "|---|---|---|\n"
        for category_name, count in sorted(protocol_counts.items()):
            file_link = f"{raw_github_base_url}/{category_name}.txt"
            md_content += f"| {category_name} | {count} | [`{category_name}.txt`]({file_link}) |\n"
    else:
        md_content += "هیچ کانفیگ پروتکلی یافت نشد.\n"
    md_content += "\n"

    md_content += "## 🌍 فایل‌های کشورها (حاوی کانفیگ)\n\n"
    if country_counts:
        md_content += "| کشور | تعداد کانفیگ مرتبط | لینک |\n"
        md_content += "|---|---|---|\n"
        for country_category_name, count in sorted(country_counts.items()):
            flag_image_markdown = "" # برای نگهداری تگ HTML تصویر پرچم
            persian_name_str = ""
            iso_code_original_case = "" # برای نگهداری کد ISO با حروف اصلی از فایل JSON

            if country_category_name in all_keywords_data:
                keywords_list = all_keywords_data[country_category_name]
                if keywords_list and isinstance(keywords_list, list):
                    # 1. پیدا کردن کد دو حرفی ISO کشور برای استفاده در URL تصویر پرچم
                    iso_code_lowercase_for_url = ""
                    for item in keywords_list:
                        if isinstance(item, str) and len(item) == 2 and item.isupper() and item.isalpha():
                            iso_code_lowercase_for_url = item.lower()
                            iso_code_original_case = item # ذخیره کد با حروف اصلی
                            break 
                    
                    if iso_code_lowercase_for_url:
                        # استفاده از flagcdn.com با عرض 20 پیکسل
                        flag_image_url = f"https://flagcdn.com/w20/{iso_code_lowercase_for_url}.png"
                        flag_image_markdown = f'<img src="{flag_image_url}" width="20" alt="{country_category_name} flag">'
                    
                    # 2. استخراج نام فارسی
                    for item in keywords_list:
                        if isinstance(item, str):
                            # از خود کد ISO (که برای پرچم استفاده شد) صرف نظر کن
                            if iso_code_original_case and item == iso_code_original_case:
                                continue
                            # از نام اصلی کشور (کلید JSON) صرف نظر کن، مگر اینکه خودش فارسی باشد (بعید)
                            if item.lower() == country_category_name.lower() and not is_persian_like(item):
                                continue
                            # از سایر کدهای دو یا سه حرفی بزرگ که کد ISO انتخاب شده نیستند، صرف نظر کن
                            if len(item) in [2,3] and item.isupper() and item.isalpha() and item != iso_code_original_case:
                                continue
                            
                            if is_persian_like(item):
                                persian_name_str = item
                                break 
            
            # 3. ساخت متن نهایی برای ستون "کشور"
            display_parts = []
            if flag_image_markdown: # اگر تگ تصویر پرچم ساخته شده باشد
                display_parts.append(flag_image_markdown)
            
            display_parts.append(country_category_name) # نام اصلی (کلید)

            if persian_name_str:
                display_parts.append(f"({persian_name_str})")
            
            country_display_text = " ".join(display_parts)
            
            file_link = f"{raw_github_base_url}/{country_category_name}.txt"
            link_text = f"{country_category_name}.txt"
            md_content += f"| {country_display_text} | {count} | [`{link_text}`]({file_link}) |\n"
    else:
        md_content += "هیچ کانفیگ مرتبط با کشوری یافت نشد.\n"
    md_content += "\n"

    try:
        with open(README_FILE, 'w', encoding='utf-8') as f:
            f.write(md_content)
        logging.info(f"Successfully generated {README_FILE}")
    except Exception as e:
        logging.error(f"Failed to write {README_FILE}: {e}")

# تابع main و سایر توابع باید مشابه نسخه کامل قبلی باشند.
# در اینجا برای کامل بودن، تابع main از پاسخ قبلی کپی می‌شود.

# --- Country Scraper Functions ---
async def fetch_github_file_list(session):
    """Fetch all txt file names from the GitHub Countries folder."""
    all_files = []
    page = 1
    while True:
        url = f"{GITHUB_COUNTRIES_API}?page={page}&per_page=100"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as resp:
                if resp.status != 200:
                    logging.error(f"GitHub API returned status {resp.status}")
                    break
                data = await resp.json()
                if not data:
                    break
                txt_files = [f for f in data if f['name'].endswith('.txt')]
                all_files.extend(txt_files)
                if len(data) < 100:
                    break
                page += 1
        except Exception as e:
            logging.error(f"Error fetching GitHub API page {page}: {e}")
            break
    return all_files

async def download_country_file(session, file_info, countries_dir):
    """Download a single country txt file from GitHub."""
    country_name = file_info['name'].replace('.txt', '')
    download_url = file_info['download_url']
    dest_path = os.path.join(countries_dir, file_info['name'])
    
    try:
        async with session.get(download_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                logging.warning(f"Failed to download {country_name}: status {resp.status}")
                return country_name, 0
            content = await resp.text()
            async with aiofiles.open(dest_path, 'w', encoding='utf-8') as f:
                await f.write(content)
            
            lines = [line.strip() for line in content.split('\n') if line.strip()]
            config_count = len(lines)
            logging.info(f"Downloaded {country_name}: {config_count} configs")
            return country_name, config_count
    except Exception as e:
        logging.error(f"Error downloading {country_name}: {e}")
        return country_name, 0

def generate_countries_readme(country_data, output_path):
    """Generate README.md for the Countries folder."""
    tz = pytz.timezone('Asia/Tehran')
    now = datetime.now(tz)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S %Z")
    
    sorted_countries = sorted(country_data.items(), key=lambda x: x[0].lower())
    total_configs = sum(count for _, count in sorted_countries)
    
    md = f"# V2Ray Configs by Country\n\n"
    md += f"> Last updated: {timestamp}\n\n"
    md += f"> Source: [SoliSpirit/v2ray-configs](https://github.com/SoliSpirit/v2ray-configs)\n\n"
    md += f"## Summary\n\n"
    md += f"- **Total Countries:** {len(sorted_countries)}\n"
    md += f"- **Total Configs:** {total_configs}\n\n"
    md += f"## Countries\n\n"
    md += f"| # | Country | Configs | Subscription Link |\n"
    md += f"|---|---------|---------|-------------------|\n"
    
    for i, (country, count) in enumerate(sorted_countries, 1):
        sub_url = f"{GITHUB_RAW_BASE}/{country}.txt"
        md += f"| {i} | **{country}** | {count} | [Subscribe]({sub_url}) |\n"
    
    md += f"\n---\n\n"
    md += f"## Usage\n\n"
    md += f"1. Choose a country from the table above\n"
    md += f"2. Click the **Subscribe** link to get the raw config file\n"
    md += f"3. Import the configs into your V2Ray client\n\n"
    md += f"## Disclaimer\n\n"
    md += f"These configs are sourced from a public repository. Use at your own risk.\n"
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(md)
    logging.info(f"Generated {output_path}")

async def scrape_countries():
    """Main function to scrape all country configs from GitHub."""
    logging.info("=== Starting Country Scraper ===")
    
    if not os.path.exists(COUNTRIES_DIR):
        os.makedirs(COUNTRIES_DIR, exist_ok=True)
        logging.info(f"Created directory: {COUNTRIES_DIR}")
    
    async with aiohttp.ClientSession() as session:
        logging.info("Fetching file list from GitHub API...")
        file_list = await fetch_github_file_list(session)
        logging.info(f"Found {len(file_list)} txt files.")
        
        if not file_list:
            logging.warning("No files found. Exiting country scraper.")
            return
        
        sem = asyncio.Semaphore(CONCURRENT_REQUESTS)
        async def download_with_sem(file_info):
            async with sem:
                return await download_country_file(session, file_info, COUNTRIES_DIR)
        
        tasks = [download_with_sem(f) for f in file_list]
        results = await asyncio.gather(*tasks)
        
        country_data = {name: count for name, count in results if count > 0}
        
        generate_countries_readme(country_data, COUNTRIES_README_FILE)
        
        logging.info(f"=== Country Scraper Finished: {len(country_data)} countries, {sum(country_data.values())} total configs ===")

async def main():
    if not os.path.exists(URLS_FILE) or not os.path.exists(KEYWORDS_FILE):
        logging.critical("Input files not found.")
        return

    with open(URLS_FILE, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip()]
    with open(KEYWORDS_FILE, 'r', encoding='utf-8') as f:
        categories_data = json.load(f)

    protocol_patterns_for_matching = {
        cat: patterns for cat, patterns in categories_data.items() if cat in PROTOCOL_CATEGORIES
    }
    country_keywords_for_naming = {
        cat: patterns for cat, patterns in categories_data.items() if cat not in PROTOCOL_CATEGORIES
    }
    country_category_names = list(country_keywords_for_naming.keys())

    logging.info(f"Loaded {len(urls)} URLs and "
                 f"{len(categories_data)} total categories from keywords.json.")

    tasks = []
    sem = asyncio.Semaphore(CONCURRENT_REQUESTS)
    async def fetch_with_sem(session, url_to_fetch):
        async with sem:
            return await fetch_url(session, url_to_fetch)
    async with aiohttp.ClientSession() as session:
        fetched_pages = await asyncio.gather(*[fetch_with_sem(session, u) for u in urls])

    final_configs_by_country = {cat: set() for cat in country_category_names}
    final_all_protocols = {cat: set() for cat in PROTOCOL_CATEGORIES}

    logging.info("Processing pages for config name association...")
    for url, text in fetched_pages:
        if not text:
            continue

        page_protocol_matches = find_matches(text, protocol_patterns_for_matching)
        all_page_configs_after_filter = set()
        for protocol_cat_name, configs_found in page_protocol_matches.items():
            if protocol_cat_name in PROTOCOL_CATEGORIES:
                for config in configs_found:
                    if should_filter_config(config):
                        continue
                    all_page_configs_after_filter.add(config)
                    final_all_protocols[protocol_cat_name].add(config)

        for config in all_page_configs_after_filter:
            name_to_check = None
            if '#' in config:
                try:
                    potential_name = config.split('#', 1)[1]
                    name_to_check = unquote(potential_name).strip()
                    if not name_to_check: name_to_check = None
                except IndexError: pass

            if not name_to_check:
                if config.startswith('ssr://'): name_to_check = get_ssr_name(config)
                elif config.startswith('vmess://'): name_to_check = get_vmess_name(config)

            if not name_to_check: continue
            
            current_name_to_check_str = name_to_check if isinstance(name_to_check, str) else ""

            for country_name_key, keywords_for_country_list in country_keywords_for_naming.items():
                text_keywords_for_country = []
                if isinstance(keywords_for_country_list, list):
                    for kw in keywords_for_country_list:
                        if isinstance(kw, str):
                            is_potential_emoji_or_short_code = (1 <= len(kw) <= 7)
                            is_alphanumeric = kw.isalnum()
                            if not (is_potential_emoji_or_short_code and not is_alphanumeric):
                                if not is_persian_like(kw):
                                     text_keywords_for_country.append(kw)
                                elif kw.lower() == country_name_key.lower():
                                    if kw not in text_keywords_for_country:
                                         text_keywords_for_country.append(kw)
                for keyword in text_keywords_for_country:
                    match_found = False
                    if not isinstance(keyword, str): continue
                    is_abbr = (len(keyword) == 2 or len(keyword) == 3) and re.match(r'^[A-Z]+$', keyword)
                    if is_abbr:
                        pattern = r'\b' + re.escape(keyword) + r'\b'
                        if re.search(pattern, current_name_to_check_str, re.IGNORECASE): match_found = True
                    else:
                        if keyword.lower() in current_name_to_check_str.lower(): match_found = True
                    if match_found:
                        final_configs_by_country[country_name_key].add(config)
                        break 
                if match_found: break

    if os.path.exists(OUTPUT_DIR): shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    logging.info(f"Saving files to directory: {OUTPUT_DIR}")

    protocol_counts = {}
    country_counts = {}

    for category, items in final_all_protocols.items():
        saved, count = save_to_file(OUTPUT_DIR, category, items)
        if saved: protocol_counts[category] = count
    for category, items in final_configs_by_country.items():
        saved, count = save_to_file(OUTPUT_DIR, category, items)
        if saved: country_counts[category] = count
    
    generate_simple_readme(protocol_counts, country_counts, categories_data, 
                           github_repo_path="miladtahanian/V2RayScrapeByCountry",
                           github_branch="main")

    await scrape_countries()

    logging.info("--- Script Finished ---")

if __name__ == "__main__":
    asyncio.run(main())
