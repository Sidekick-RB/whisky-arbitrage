import os
from flask import Flask, jsonify
from scraper import get_live_twe_data
from agents import get_clean_search_term
from whiskybase import get_reference_price

app = Flask(__name__)

def calculate_arbitrage(scraped_data):
    opportunities = []

    for item in scraped_data:
        raw_name = item["name"]
        twe_price = item["price"]
        
        print(f"\nProcessing: {raw_name}")
        
        # 1. AI cleans the name for search
        clean_name = get_clean_search_term(raw_name)
        print(f"AI Cleaned Name: {clean_name}")
        
        # 2. Scrape live market value from Whiskybase
        market_price = get_reference_price(clean_name)
        print(f"Whiskybase Market Price: £{market_price}")
        
        profit = market_price - twe_price
        roi = (profit / twe_price) * 100
        
        # Only add to deals if it's actually profitable
        if profit > 0:
            opportunities.append({
                "bottle_scraped": raw_name,
                "wb_search_term": clean_name,
                "buy_at_twe_gbp": twe_price,
                "wb_market_value_gbp": market_price,
                "potential_profit_gbp": round(profit, 2),
                "roi_percent": round(roi, 2)
            })
                
    # Sort the final list so the most profitable deals are at the top
    return sorted(opportunities, key=lambda x: x["potential_profit_gbp"], reverse=True)

@app.route('/scan', methods=['GET'])
def scan():
    try:
        # Step 1: Get live TWE retail prices
        live_data = get_live_twe_data("macallan")
        
        # Step 2 & 3: Clean names and get Whiskybase prices 
        # (Limiting to the first 3 items during testing)
        opportunities = calculate_arbitrage(live_data[:3]) 
        
        return jsonify({
            "status": "success",
            "source": "TWE + Whiskybase Live (via AI)",
            "arbitrage_deals": opportunities
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    # Render assigns a specific port in the cloud, otherwise default to 5000 locally
    port = int(os.environ.get("PORT", 5000))
    # host='0.0.0.0' tells Flask to broadcast to the outside world
    app.run(host='0.0.0.0', port=port)
