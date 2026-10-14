import { useLayoutEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { getRouteTitle } from '../lib/routeTitles';

export default function RouteTitle() {
  const { pathname } = useLocation();

  useLayoutEffect(() => {
    document.title = getRouteTitle(pathname);
  }, [pathname]);

  return null;
}
