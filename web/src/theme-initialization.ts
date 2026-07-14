import { ThemeEnum } from '@/constants/common';

export const RAGFLOW_THEME_STORAGE_KEY = 'ragflow-ui-theme';
export const PORTAL_EMBED_THEME_STORAGE_KEY = 'ragflow-portal-embed-ui-theme';

type ThemeProviderConfig = {
  defaultTheme: ThemeEnum;
  storageKey: string;
};

export function getThemeProviderConfig(search: string): ThemeProviderConfig {
  const defaultTheme = new URLSearchParams(search).get('default_theme');

  if (defaultTheme === ThemeEnum.Light || defaultTheme === ThemeEnum.Dark) {
    return {
      defaultTheme,
      storageKey: PORTAL_EMBED_THEME_STORAGE_KEY,
    };
  }

  return {
    defaultTheme: ThemeEnum.Dark,
    storageKey: RAGFLOW_THEME_STORAGE_KEY,
  };
}
