import { matchRoutes } from 'react-router-dom';
import { COMPANY_NAME } from './siteIdentity';

// Keep fixed page names here, never project names or other user-supplied content.
export const routeTitles = [
  { path: '/', pageName: '' },
  { path: '/login', pageName: '登录' },
  { path: '/change-password', pageName: '修改密码' },
  { path: '/products/new', pageName: '创建商品' },
  { path: '/products', pageName: '商品管理' },
  { path: '/products/:id', pageName: '商品详情' },
  { path: '/knowledge', pageName: '知识库' },
  { path: '/design-skills', pageName: '设计 Skill' },
  { path: '/brand-visuals', pageName: '品牌视觉' },
  { path: '/task-center', pageName: '任务与费用' },
  { path: '/detail-templates', pageName: '详情页模板' },
  { path: '/production', pageName: '批量生产' },
  { path: '/quality', pageName: '质量中心' },
  { path: '/creative-projects', pageName: 'AI 创作工作台' },
  { path: '/creative-projects/:id', pageName: '创意画布' },
  { path: '/creative-projects/:id/storyboard', pageName: '详情页编排' },
  { path: '/video-projects', pageName: 'AI 口播视频' },
  { path: '/video-projects/:id', pageName: '口播视频编辑' },
  { path: '/director-inspiration', pageName: '编导灵感' },
  { path: '/director-inspiration/:id', pageName: '编导分析' },
  { path: '/director-inspiration/:id/workspace', pageName: '编导工作台' },
  { path: '/shooting-tasks', pageName: '拍摄任务' },
  { path: '/semi-finished-assets', pageName: '半成品素材库' },
  { path: '/semi-finished-assets/:id', pageName: '半成品素材详情' },
  { path: '/finished-video-assets', pageName: '成品素材库' },
  { path: '/finished-video-assets/:id', pageName: '成品素材详情' },
  { path: '/final-videos', pageName: '成片库' },
  { path: '/final-videos/:id', pageName: '成片详情' },
  { path: '/qianchuan/oauth/result', pageName: '千川授权结果' },
  { path: '/manual-video-compose', pageName: '视频编排' },
  { path: '/manual-video-compose/:id', pageName: '视频编排编辑' },
  { path: '/platform-admin', pageName: '平台管理后台' },
  { path: '/tenant-admin', pageName: '企业管理后台' },
  { path: '/tenant-admin/roles', pageName: '角色与权限' },
  { path: '/tenant-admin/taxonomy', pageName: '视频分类管理' },
  { path: '/smart-video-compose', pageName: '智能成片' },
  { path: '/smart-video-compose/:id', pageName: '智能成片编辑' },
  { path: '/finished-video-remix', pageName: '成品混剪' },
  { path: '/finished-video-remix/:id', pageName: '成品混剪编辑' },
  { path: '/performance-video-projects/:id', pageName: '视频自动剪辑' },
  // Legacy entry points use the destination title even before Navigate commits.
  { path: '/video-taxonomy', pageName: '视频分类管理' },
  { path: '/performance-video-projects', pageName: '智能成片' },
  { path: '/video-assets', pageName: '半成品素材库' },
  { path: '*', pageName: '' },
];

export function getRouteTitle(pathname: string): string {
  const matches = matchRoutes(routeTitles, { pathname });
  const pageName = matches?.[matches.length - 1].route.pageName;
  return pageName ? `${pageName} - ${COMPANY_NAME}` : COMPANY_NAME;
}
