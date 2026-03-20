from flask import Flask, jsonify
from scraper import get_live_twe_data

app = Flask(__name__)

# --- MOCK REFERENCE DATABASE (To be replaced by LangChain / Live APIs later) ---
REFERENCE_MARKET = {
    "MACALLAN 12 YEAR OLD SHERRY OAK": 110.00,  # Example: Market Value £110
    "MACALLAN 15 YEAR OLD DOUBLE CASK": 130.00, # Example: Market Value £130
    "MACALLAN 18 YEAR OLD SHERRY OAK 2024 Release": 420.00, # Example: Market Value £420
}

def calculate_arbitrage(scraped_data):
    opportunities = []
    for item in scraped_data:
        name = item["name"]
        twe_price = item["price"]
        
        # Simple exact match for now (LangChain Pricing Agent will handle fuzzy matching later)
        # Note: We use .upper() to help with basic case-insensitivity
        ref_keys = {k.upper(): v for k, v in REFERENCE_MARKET.items()}
        
        if name.upper() in ref_keys:
            market_price = ref_keys[name.upper()]
            profit = market_price - twe_price
            roi = (profit / twe_price) * 100
            
            if profit > 0:
                opportunities.append({
                    "bottle": name,
                    "buy_at_twe": twe_price,
                    "sell_at_market": market_price,
                    "potential_profit_gbp": round(profit, 2),
                    "roi_percent": round(roi, 2)
                })
    
    # Sort by most profitable
    return sorted(opportunities, key=lambda x: x["potential_profit_gbp"], reverse=True)

@app.route('/scan', methods=['GET'])
def scan():
    try:
        live_data = get_live_twe_data("macallan")
        opportunities = calculate_arbitrage(live_data)
        
        return jsonify({
            "status": "success",
            "source": "The Whisky Exchange (Live via Playwright)",
            "scraped_items_count": len(live_data),
            "opportunities_found": len(opportunities),
            "arbitrage_deals": opportunities,
            "raw_data": live_data
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

if __name__ == '__main__':
    app.run(debug=True)
