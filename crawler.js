const { execSync } = require('child_process');
const https = require('https');

const SERVER_URL = process.env.SERVER_URL || 'https://yystudy.top/flight';
const SERVER_TOKEN = process.env.SERVER_TOKEN || '';

// 测试航线（城市用英文！）
const TEST_ROUTES = [
  { dep: 'Chengdu', arr: 'Shanghai', date: '2026-12-15' },
  { dep: 'Chengdu', arr: 'Beijing', date: '2026-12-20' },
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
      const cmd = `flyai search-flight --origin "${route.dep}" --destination "${route.arr}" --dep-date ${route.date} --seat-class-name economy`;
      console.log(`CMD: ${cmd}`);
      const output = execSync(cmd, { encoding: 'utf8', timeout: 30000 });
      console.log(`Output length: ${output.length}`);
      console.log(`Output first 1000: ${output.substring(0, 1000)}`);
      
      const result = JSON.parse(output);
      const itemList = result.data?.itemList || [];
      console.log(`Got ${itemList.length} items`);
      
      const prices = itemList.map(item => parseFloat(item.ticketPrice)).filter(p => p > 0);
      if (prices.length > 0) {
        allResults.push({
          dep: route.dep,
          arr: route.arr,
          date: route.date,
          platform: '飞猪(flyai)',
          prices: prices,
        });
        console.log(`Prices: min=${Math.min(...prices)}, max=${Math.max(...prices)}`);
      }
    } catch (err) {
      console.log(`Error: ${err.message}`);
      if (err.stdout) console.log(`stdout: ${err.stdout.substring(0, 500)}`);
      if (err.stderr) console.log(`stderr: ${err.stderr.substring(0, 500)}`);
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
