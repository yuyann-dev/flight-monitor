const { execSync } = require('child_process');
const https = require('https');

const SERVER_URL = process.env.SERVER_URL || 'https://yystudy.top/flight';
const SERVER_TOKEN = process.env.SERVER_TOKEN || '';

const TEST_ROUTES = [
  { dep: '成都', arr: '上海', date: '2026-12-15' },
  { dep: '成都', arr: '北京', date: '2026-12-20' },
];

function postResults(results) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(results);
    const url = new URL(`${SERVER_URL}/api/crawler-results`);
    const req = https.request({
      hostname: url.hostname,
      path: url.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${SERVER_TOKEN}`,
        'Content-Length': data.length,
      },
    }, (res) => {
      console.log(`POST status: ${res.statusCode}`);
      resolve();
    });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

async function main() {
  const allResults = [];

  for (const route of TEST_ROUTES) {
    try {
      console.log(`Searching: ${route.dep} -> ${route.arr} on ${route.date}`);
      const cmd = `flyai search-flight --origin "${route.dep}" --destination "${route.arr}" --dep-date ${route.date} --journey-type 1 --sort-type 3`;
      const output = execSync(cmd, { encoding: 'utf8', timeout: 60000 });
      console.log(`Output: ${output.substring(0, 500)}`);
      
      try {
        const result = JSON.parse(output);
        const flights = result.flights || result.data || [];
        const prices = flights.map(f => f.price || f.adult_price).filter(Boolean);
        if (prices.length > 0) {
          allResults.push({
            dep: route.dep,
            arr: route.arr,
            date: route.date,
            platform: '飞猪(flyai)',
            prices: prices.map(p => Number(p)),
          });
          console.log(`Got ${prices.length} prices, min=${Math.min(...prices)}`);
        }
      } catch (parseErr) {
        console.log(`Parse error: ${parseErr.message}`);
      }
    } catch (err) {
      console.log(`Search error: ${err.message}`);
    }
  }

  if (allResults.length > 0) {
    await postResults(allResults);
    console.log(`Posted ${allResults.length} routes to server`);
  } else {
    console.log('No results found');
  }
}

main().catch(console.error);
