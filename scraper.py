import asyncio
from playwright.async_api import async_playwright

async def scrape_twe_async(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True) 
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        try:
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_selector('.product-card', timeout=15000)
            
            products = await page.query_selector_all('.product-card')
            results = []

            for product in products[:10]:
                try:
                    name_raw = await (await product.query_selector('.product-card__name')).inner_text()
                    name = name_raw.replace('\n', ' ').strip()
                    
                    price_text = await (await product.query_selector('.product-card__price')).inner_text()
                    price = float(price_text.replace('£', '').replace(',', '').strip())
                    
                    results.append({"name": name, "price": price})
                except Exception as e:
                    print(f"Skipped product: {e}")
            
            return results
        finally:
            await browser.close()

def get_live_twe_data(search_term="macallan"):
    url = f"https://www.thewhiskyexchange.com/search?q={search_term}"
    return asyncio.run(scrape_twe_async(url))
