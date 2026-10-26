import { apiRequest } from '../client.js'

/**
 * Fetch seeded demo users (no auth required).
 * @returns {Promise<import('../schema').DemoUserView[]>}
 */
export function getDemoUsers() {
  return apiRequest('/demo/users')
}
