import http from 'k6/http';
import { check, sleep } from 'k6';

import { recordStatus } from './lib/metrics.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const KNOWN_MESSAGE_ID = Number(__ENV.MESSAGE_ID || '1');

export const options = {
  stages: [
    { duration: '30s', target: 25 },
    { duration: '1m', target: 100 },
    { duration: '1m', target: 300 },
    { duration: '1m', target: 600 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.20'],
    http_req_duration: ['p(95)<750'],
  },
};

function postMessage() {
  const payload = JSON.stringify({
    content: `k6 message from vu=${__VU} iter=${__ITER}`,
  });

  return http.post(`${BASE_URL}/messages`, payload, {
    headers: { 'Content-Type': 'application/json' },
  });
}

function getMessage() {
  const shouldMissCache = Math.random() < 0.2;
  const messageId = shouldMissCache ? KNOWN_MESSAGE_ID + 1000000 + __ITER : KNOWN_MESSAGE_ID;
  return http.get(`${BASE_URL}/messages/${messageId}`);
}

export default function () {
  const response = Math.random() < 0.2 ? postMessage() : getMessage();
  recordStatus(response);

  check(response, {
    'mixed traffic returned expected status': (res) =>
      [200, 201, 404, 429, 503].includes(res.status),
  });

  sleep(0.1);
}
