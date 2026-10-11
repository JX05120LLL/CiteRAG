import type { ThemeConfig } from 'antd';

export const palette = {
  primary: '#276650', primaryHover: '#1d503e', primarySelected: '#194a37',
  canvas: '#f4f3ef', surface: '#ffffff', surfaceTint: '#eef2e9', softGreen: '#e8efdf',
  ink: '#1c3028', muted: '#627167', line: '#dedfd8', disabled: '#626e65',
  navigation: '#142621', navigationHover: '#243c32', navigationLine: '#365046',
  navigationText: '#f4f3ef', navigationMuted: '#b3c3b9', mint: '#bcf48c',
  error: '#b23b36', warning: '#94611b',
} as const;

// Shared by the workbench, management surfaces and both voice entries.
export const theme = {
  token: {
    colorPrimary: palette.primary, colorPrimaryHover: palette.primaryHover, colorPrimaryActive: palette.primarySelected,
    colorSuccess: palette.primary, colorInfo: palette.primary, colorError: palette.error, colorWarning: palette.warning,
    colorSuccessBg: palette.softGreen, colorInfoBg: palette.softGreen, colorWarningBg: '#fff0d5', colorErrorBg: '#fae9e6',
    colorText: palette.ink, colorTextHeading: palette.ink, colorTextSecondary: palette.muted,
    colorTextDescription: palette.muted, colorTextPlaceholder: palette.muted,
    colorTextDisabled: palette.disabled, colorBgContainerDisabled: '#eeefea',
    colorLink: palette.primary, colorLinkHover: palette.primaryHover, colorLinkActive: palette.primarySelected,
    colorBgLayout: palette.canvas, colorBgContainer: palette.surface,
    colorBorder: palette.line, colorBorderSecondary: '#e8e9e2',
    borderRadius: 10, borderRadiusLG: 14, controlHeight: 36, fontSize: 14, lineHeight: 1.65,
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif',
  },
  components: {
    Button: { primaryShadow: 'none', defaultShadow: 'none' },
    Menu: { itemColor: palette.navigationText, itemSelectedBg: palette.mint, itemSelectedColor: palette.navigation,
      itemHoverBg: palette.navigationHover, itemHoverColor: palette.navigationText,
      itemDisabledColor: '#99b4a7', itemHeight: 44 },
    Card: { headerFontSize: 16 },
    Descriptions: { labelColor: palette.muted, contentColor: palette.ink },
    Tag: { defaultBg: '#eeefea', defaultColor: palette.disabled },
    Collapse: { headerBg: palette.canvas },
  },
} satisfies ThemeConfig;
