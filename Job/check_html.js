const fs = require('fs');
const html = fs.readFileSync('web-ui/static/index.html', 'utf8');

console.log('=== 1. Checking switchTab function ===');
const switchTabMatch = html.match(/function switchTab\(tabId\)[\s\S]*?\n    \}/);
if (switchTabMatch) {
  console.log('switchTab function found');
  const body = switchTabMatch[0];
  console.log('  Contains loadReviewQueue():', loadReviewQueue);
  if (loadReviewQueue) {
    console.log('  FOUND: loadReviewQueue() - WRONG, should be renderReviewQueue()');
  }
  if (renderReviewQueue) {
    console.log('  FOUND: renderReviewQueue() - correct');
  }
  if (loadAnalytics) {
    console.log('  FOUND: loadAnalytics() - WRONG, should be renderAnalytics()');
  }
  if (renderAnalytics) {
    console.log('  FOUND: renderAnalytics() - correct');
  }
} else {
  console.log('switchTab function not found');
}

console.log('\n=== 2. Checking for `metrics.status_breakdown || {}` (line ~368) ===');
const statusMatch = html.match(/scores = Object\.entries\((.*)\)/);
if (statusMatch) {
  console.log('statuses expression:', statusMatch[1]);
  if (statusMatch[1] === '(metrics.status_breakdown || {})') {
    console.log('✓ Already has (metrics && metrics.status_breakdown) || {}');
  } else if (statusMatch[1] === 'metrics.status_breakdown || {}') {
    console.log('❌ Missing null-check for metrics object itself');
  } else {
    console.log('Unexpected pattern:', statusMatch[1]);
  }
} else {
  console.log('No match found for Object.entries(metrics.status_breakdown');
}