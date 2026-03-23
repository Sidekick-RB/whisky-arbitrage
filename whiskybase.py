import asyncio
import re
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

async def search_whiskybase_async(search_term):
    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",  
                "--no-sandbox",             
                "--disable-gpu"             
            ]
        )
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        
        # Put the browser on a diet: Block images, CSS, media, and fonts to save RAM
        async def intercept_route(route):
            if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
                await route.abort()
            else:
                await route.continue_()
                
        await page.route("**/*", intercept_route)
        
        try:
            print(f"\n--- Searching Whiskybase for: {search_term} ---")
            search_url = f"https://www.whiskybase.com/search?q={search_term.replace(' ', '+')}"
            await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)
            
            links = await page.query_selector_all('table tbody tr a')
            urls_to_check = []
            
            for link in links[:3]:
                href = await link.get_attribute('href')
                if href and '/whisky/' in href:
                    urls_to_check.append(href)
            
            if not urls_to_check:
                return None
                
            for url in urls_to_check:
                await asyncio.sleep(2)
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(3) 
                
                detail_text = await page.inner_text('body')
                
                if "Verify you are human" in detail_text or "cloudflare" in detail_text.lower():
                    continue 
                
                detail_prices = re.findall(r'(?:€|EUR)\s*([0-9]+[.,][0-9]+)', detail_text)
                
                if detail_prices:
                    clean_price = float(detail_prices[0].replace(',', '.'))
                    return round(clean_price * 0.85, 2)
                    
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
