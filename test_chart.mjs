import { chromium } from 'playwright';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
await page.goto('http://localhost:5001/planner');
// Wait for Chart.js to render
await page.waitForTimeout(3000);

// Screenshot just the chart card area
const chartCard = page.locator('.chart-card');
await chartCard.screenshot({ path: 'screenshots/chart_test.png' });

// Also take full page screenshot
await page.screenshot({ path: 'screenshots/full_page_test.png', fullPage: true });

console.log('Screenshots saved to screenshots/');
await browser.close();
