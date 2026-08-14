import http from 'k6/http';
import { check, sleep } from 'k6';

import { recordStatus } from './lib/metrics.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const MESSAGE_ID = __ENV.MESSAGE_ID || '1';

export const options = {
  scenarios: {
    trigger_rate_limit: {
      executor: 'constant-vus',
      vus: 5,
      duration: '20s',
    },
  },
  thresholds: {
    status_429: ['count>0'],
  },
};

export default function () {
  const response = http.get(`${BASE_URL}/messages/${MESSAGE_ID}`);
  recordStatus(response);

  check(response, {
    'rate limit test returned 200 or 429': (res) => [200, 429].includes(res.status),
  });

  sleep(0.05);
}
