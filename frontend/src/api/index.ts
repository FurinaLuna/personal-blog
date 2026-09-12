/** API 层出口。组件里统一从 `@/api` 引入，不直接 import 具体文件。
 *
 * 模块按后端领域一一对应：articles / attachments / auth / comments /
 * site / taxonomy，一个文件一个领域，不混装。
 */
export { ApiError, api, http, request, tokenStore } from './http'
export { articleApi } from './articles'
export { attachmentApi } from './attachments'
export { authApi } from './auth'
export { commentApi } from './comments'
export { siteApi } from './site'
export { categoryApi, tagApi } from './taxonomy'
