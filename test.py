import flask
import json

app = flask.Flask(__name__)

@app.route('/')
def home():
    return 'Whisky Arbitrage Test OK!'

@app.route('/scan')
def scan():
    data = {'opportunities': 2, 'status': 'working'}
    return flask.Response(json.dumps(data), mimetype='application/json')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
