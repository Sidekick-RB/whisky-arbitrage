import asyncio
import re
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

async def search_whiskybase_async(search_term):
    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        
        try:
            print(f"\n--- Searching Whiskybase for: {search_term} ---")
            search_url = f"https://www.whiskybase.com/search?q={search_term.replace(' ', '+')}"
            await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)
            
            # Grab the top 3 links from the search results table
            links = await page.query_selector_all('table tbody tr a')
            urls_to_check = []
            
            for link in links[:3]:
                href = await link.get_attribute('href')
                # Ensure it's an actual bottle link, not a distillery link
                if href and '/whisky/' in href:
                    urls_to_check.append(href)
            
            if not urls_to_check:
                print("No bottle links found in search results.")
                return None
                
            print(f"Found {len(urls_to_check)} potential bottles. Hunting for prices...")
            
            # Loop through the top URLs until we find a price
            for url in urls_to_check:
                await asyncio.sleep(2) # Mimic human delay
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(3) 
                
                detail_text = await page.inner_text('body')
                
                if "Verify you are human" in detail_text or "cloudflare" in detail_text.lower():
                    print("⚠️ Cloudflare block on this page, skipping to next...")
                    continue 
                
                detail_prices = re.findall(r'(?:€|EUR)\s*([0-9]+[.,][0-9]+)', detail_text)
                
                if detail_prices:
                    clean_price = float(detail_prices[0].replace(',', '.'))
                    print(f"✅ Success! Found price: €{clean_price}")
                    return round(clean_price * 0.85, 2)
                    
                print("No price on this bottle, trying the next one...")
            
            print("❌ Checked top 3 results, no prices found.")
            return None
            
        except Exception as e:
            print(f"Whiskybase scrape error: {e}")
            return None
        finally:
            await browser.close()

def get_reference_price(search_term):
    price = asyncio.run(search_whiskybase_async(search_term))
    if not price:
        return 120.00 
    return price
