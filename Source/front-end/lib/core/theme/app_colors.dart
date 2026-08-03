import 'package:flutter/material.dart';

/// Color tokens from design/FRONTEND_STYLE.md's "Arena" visual language.
/// Never hardcode a hex value outside this file -- reference these names.
class AppColors {
  AppColors._();

  // Surfaces
  static const bg = Color(0xFF0a0e17);
  static const bgDeep = Color(0xFF070a12);
  static const surface = Color(0x09FFFFFF);
  static const surfaceGrad = [Color(0x0DFFFFFF), Color(0x05FFFFFF)];
  static const inset = Color(0x08FFFFFF);
  static const border = Color(0x12FFFFFF);
  static const borderRaised = Color(0x14FFFFFF);

  // Text
  static const ink = Color(0xFFEAF0F7);
  static const inkMid = Color(0xFFCDD5DE);
  static const inkSub = Color(0xFF8A96A8);
  static const inkMute = Color(0xFF586577);

  // Accents
  static const cyan = Color(0xFF22D3EE);
  static const cyan2 = Color(0xFF5EEAD4);
  static const violet = Color(0xFF7C6CFF);
  static const violet2 = Color(0xFFA99DFF);

  // Signal
  static const pos = Color(0xFF22D3EE);
  static const neg = Color(0xFFFF5C7A);
  static const neg2 = Color(0xFFFF8FA3);
  static const live = Color(0xFF4ADE80);
  static const warn = Color(0xFFFFB454);

  // Gradients
  static const brandMark = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [cyan, violet],
  );
  static const cyanFill = LinearGradient(colors: [cyan, cyan2]);
  static const negFill = LinearGradient(colors: [neg, neg2]);
  static const accentStripH2h = LinearGradient(colors: [cyan, cyan2]);
  static const accentStripField = LinearGradient(colors: [violet, cyan]);
  static const glow = RadialGradient(
    colors: [Color(0x1A22D3EE), Color(0x0D7C6CFF), Colors.transparent],
    stops: [0, 0.45, 0.70],
  );
}
