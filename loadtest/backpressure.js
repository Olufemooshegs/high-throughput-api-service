import http from 'k6/http';
import { check } from 'k6';

import { recordStatus } from './lib/metrics.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  scenarios: {
    burst: {
      executor: 'constant-vus',
      vus: 300,
      duration: '20s',
    },
  },
  thresholds: {
    status_503: ['count>0'],
  },
};

export default function () {
  const response = http.get(`${BASE_URL}/health`);
  recordStatus(response);

  check(response, {
    'backpressure test returned 200 or 503': (res) => [200, 503].includes(res.status),
    '503 includes Retry-After': (res) =>
      res.status !== 503 || res.headers['Retry-After'] !== undefined,
  });
}
