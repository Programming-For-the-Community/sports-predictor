import 'package:flutter/material.dart';

/// Single-line text that shrinks to fit its cell instead of ellipsizing.
/// For short, fixed-width cells (column headers like "CHAMP%", positions
/// like "T123", percentages) where a "..." hides the value itself. Long,
/// free-form text (names, venues) should wrap in a plain Text instead.
class FitText extends StatelessWidget {
  const FitText(this.data, {super.key, this.style, this.textAlign});

  final String data;
  final TextStyle? style;
  final TextAlign? textAlign;

  @override
  Widget build(BuildContext context) {
    final alignment = switch (textAlign) {
      TextAlign.start || TextAlign.left => Alignment.centerLeft,
      TextAlign.end || TextAlign.right => Alignment.centerRight,
      _ => Alignment.center,
    };
    return FittedBox(
      fit: BoxFit.scaleDown,
      alignment: alignment,
      child: Text(data, style: style, textAlign: textAlign, maxLines: 1, softWrap: false),
    );
  }
}
