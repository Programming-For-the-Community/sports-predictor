import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

import 'app_colors.dart';

/// Type scale from design/FRONTEND_STYLE.md. Two families only: Space
/// Grotesk for headings/prose/names, IBM Plex Mono for measured values and
/// labels -- never mix the two roles.
class AppTextStyles {
  AppTextStyles._();

  static TextStyle _display({
    required double size,
    required FontWeight weight,
    double? letterSpacing,
    Color color = AppColors.ink,
  }) =>
      GoogleFonts.spaceGrotesk(
        fontSize: size,
        fontWeight: weight,
        letterSpacing: letterSpacing,
        color: color,
      );

  static TextStyle _mono({
    required double size,
    FontWeight weight = FontWeight.w400,
    double? letterSpacing,
    Color color = AppColors.ink,
  }) =>
      GoogleFonts.ibmPlexMono(
        fontSize: size,
        fontWeight: weight,
        letterSpacing: letterSpacing,
        color: color,
      );

  /// clamp(32px, 4.5vw, 52px) collapses to a fixed size in Flutter -- the
  /// responsive clamp is handled by LayoutBuilder at the call site instead.
  static TextStyle pageH1({Color color = AppColors.ink}) =>
      _display(size: 40, weight: FontWeight.w700, letterSpacing: -0.025 * 40, color: color);

  static TextStyle bigStatNumeral({Color color = AppColors.ink}) =>
      _display(size: 44, weight: FontWeight.w700, color: color);

  static TextStyle cardTitle({Color color = AppColors.ink}) =>
      _display(size: 26, weight: FontWeight.w700, color: color);

  static TextStyle sectionTitle({Color color = AppColors.ink}) =>
      _display(size: 16, weight: FontWeight.w600, color: color);

  static TextStyle body({Color color = AppColors.inkMid}) =>
      _display(size: 15, weight: FontWeight.w500, color: color);

  /// Uppercase, wide-tracked mono -- apply .toUpperCase() at the call site.
  static TextStyle microLabel({Color color = AppColors.inkMute}) =>
      _mono(size: 11, weight: FontWeight.w600, letterSpacing: 0.16 * 11, color: color);

  static TextStyle metricValue({Color color = AppColors.ink}) =>
      _mono(size: 16, weight: FontWeight.w600, color: color);

  static TextStyle metricValueLarge({Color color = AppColors.ink}) =>
      _mono(size: 19, weight: FontWeight.w700, color: color);
}
