import http from 'k6/http';
import { check, sleep } from 'k6';

import { recordStatus } from './lib/metrics.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const MESSAGE_ID = __ENV.MESSAGE_ID || '1';

export const options = {
  stages: [
    { duration: '30s', target: 10 },
    { duration: '1m', target: 50 },
    { duration: '1m', target: 200 },
    { duration: '1m', target: 500 },
    { duration: '1m', target: 1000 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.10'],
    http_req_duration: ['p(95)<500'],
  },
};

export default function () {
  const response = http.get(`${BASE_URL}/messages/${MESSAGE_ID}`);
  recordStatus(response);

  check(response, {
    'GET returned 200': (res) => res.status === 200,
  });

  sleep(0.1);
}
