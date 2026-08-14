import { Counter } from 'k6/metrics';

export const status200 = new Counter('status_200');
export const status201 = new Counter('status_201');
export const status404 = new Counter('status_404');
export const status429 = new Counter('status_429');
export const status503 = new Counter('status_503');
export const statusOther = new Counter('status_other');

export function recordStatus(response) {
  if (response.status === 200) {
    status200.add(1);
  } else if (response.status === 201) {
    status201.add(1);
  } else if (response.status === 404) {
    status404.add(1);
  } else if (response.status === 429) {
    status429.add(1);
  } else if (response.status === 503) {
    status503.add(1);
  } else {
    statusOther.add(1);
  }
}
