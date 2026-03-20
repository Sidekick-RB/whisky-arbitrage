import flask
import json

app = flask.Flask(__name__)

@app.route('/')
def home():
    return 'Whisky Arbitrage Orchestrator v1.0 🚀'

@app.route('/scan')
def scan_whisky():
    # Mock auction listings
    listings = [
        {'name': 'Macallan 12 Sherry Oak', 'price': 45, 'source': 'auction-x.com'},
        {'name': 'Glenfiddich 18', 'price': 120, 'source': 'auction-x.com'},
        {'name': 'Lagavulin 16', 'price': 85, 'source': 'retail-y.com'},
    ]
    
    # Global benchmarks
    benchmarks = {
        'Macallan 12 Sherry Oak': 80,
        'Glenfiddich 18': 160,
        'Lagavulin 16': 110,
    }
    
    opportunities = []
    for item in listings:
        benchmark = benchmarks.get(item['name'], 100)
        if item['price'] < benchmark * 0.8:  # 20% below market
            opportunities.append({
                'name': item['name'],
                'buy_price': item['price'],
                'benchmark': benchmark,
                'potential_profit': benchmark * 0.9 - item['price'],
                'source': item['source']
            })
    
    return flask.Response(json.dumps({
        'timestamp': '2026-03-20T10:44',
        'opportunities': opportunities,
        'total_found': len(opportunities),
        'message': f'Found {len(opportunities)} resale opportunities!'
    }), mimetype='application/json')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
