import flask
import json
from scraper import get_multiple_sources, get_price_benchmark

app = flask.Flask(__name__)

@app.route('/')
def home():
    return 'Whisky Arbitrage v2.3 - Real Scraping 🚀'

@app.route('/scan')
def scan_whisky():
    listings = get_multiple_sources(limit=10)
    
    opportunities = []
    for listing in listings:
        benchmark = get_price_benchmark(listing['name'])
        
        if listing['price_gbp'] > 0 and listing['price_gbp'] < benchmark * 0.85:
            opportunities.append({
                'name': listing['name'],
                'buy_price': round(listing['price_gbp'], 1),
                'benchmark': round(benchmark, 1),
                'profit_gbp': round((benchmark * 0.9) - listing['price_gbp'], 1),
                'source': listing['source']
            })
    
    return flask.Response(json.dumps({
        'scraped': len(listings),
        'opportunities': opportunities,
        'summary': f'{len(opportunities)} deals found'
    }, indent=2), mimetype='application/json')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
