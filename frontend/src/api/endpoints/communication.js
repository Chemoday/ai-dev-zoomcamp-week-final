import { apiRequest } from '../client.js'

/**
 * Fetch active emergency alerts targeting the caller.
 * @returns {Promise<import('../schema').EmergencyView[]>}
 */
export function getActiveEmergencies() {
  return apiRequest('/communication/emergencies/active')
}

/**
 * Acknowledge an active emergency alert.
 * @param {string} postId
 * @returns {Promise<import('../schema').AcknowledgeResponse>}
 */
export function acknowledgeEmergency(postId) {
  return apiRequest(`/communication/emergencies/${encodeURIComponent(postId)}/acknowledge`, {
    method: 'POST',
  })
}

/**
 * Resolve an active emergency alert (Board / Chairman only).
 * @param {string} postId
 * @returns {Promise<import('../schema').ResolveResponse>}
 */
export function resolveEmergency(postId) {
  return apiRequest(`/communication/emergencies/${encodeURIComponent(postId)}/resolve`, {
    method: 'POST',
  })
}

/**
 * List posts with optional filters and paging.
 * @param {{ post_type?: string | null, category?: string | null, limit?: number, offset?: number }} [params]
 * @returns {Promise<import('../schema').PostView[]>}
 */
export function listPosts(params = {}) {
  const search = new URLSearchParams()
  if (params.post_type) search.set('post_type', params.post_type)
  if (params.category) search.set('category', params.category)
  if (params.limit !== undefined) search.set('limit', String(params.limit))
  if (params.offset !== undefined) search.set('offset', String(params.offset))
  const qs = search.toString()
  return apiRequest(`/communication/posts${qs ? `?${qs}` : ''}`)
}

/**
 * Create a new post (forum topic, official notice, or emergency).
 * @param {import('../schema').PostCreateRequest} payload
 * @returns {Promise<import('../schema').PostView>}
 */
export function createPost(payload) {
  return apiRequest('/communication/posts', {
    method: 'POST',
    body: payload,
  })
}

/**
 * Get post detail with paged comments.
 * @param {string} postId
 * @param {{ comments_limit?: number, comments_offset?: number }} [params]
 * @returns {Promise<import('../schema').PostDetail>}
 */
export function getPost(postId, params = {}) {
  const search = new URLSearchParams()
  if (params.comments_limit !== undefined) search.set('comments_limit', String(params.comments_limit))
  if (params.comments_offset !== undefined) search.set('comments_offset', String(params.comments_offset))
  const qs = search.toString()
  return apiRequest(`/communication/posts/${encodeURIComponent(postId)}${qs ? `?${qs}` : ''}`)
}

/**
 * Delete a forum topic (Chairman only).
 * @param {string} postId
 * @returns {Promise<null>}
 */
export function deletePost(postId) {
  return apiRequest(`/communication/posts/${encodeURIComponent(postId)}`, {
    method: 'DELETE',
  })
}

/**
 * Add a comment to a post.
 * @param {string} postId
 * @param {import('../schema').CommentCreateRequest} payload
 * @returns {Promise<import('../schema').CommentView>}
 */
export function addComment(postId, payload) {
  return apiRequest(`/communication/posts/${encodeURIComponent(postId)}/comments`, {
    method: 'POST',
    body: payload,
  })
}
