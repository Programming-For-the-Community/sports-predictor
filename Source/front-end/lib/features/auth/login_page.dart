import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/auth/auth_repository.dart';
import '../../core/auth/cognito_auth_client.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import '../../core/widgets/brand_mark.dart';
import '../../core/widgets/page_glow.dart';

class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});

  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends ConsumerState<LoginPage> {
  final _usernameController = TextEditingController();
  final _passwordController = TextEditingController();
  final _newPasswordController = TextEditingController();

  bool _isSubmitting = false;
  String? _errorMessage;

  @override
  void dispose() {
    _usernameController.dispose();
    _passwordController.dispose();
    _newPasswordController.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() {
      _isSubmitting = true;
      _errorMessage = null;
    });
    try {
      await action();
    } catch (e) {
      if (mounted) setState(() => _errorMessage = _messageFor(e));
    } finally {
      if (mounted) setState(() => _isSubmitting = false);
    }
  }

  String _messageFor(Object error) {
    if (error is CognitoException) {
      if (error.type.contains('NotAuthorizedException')) {
        return 'Incorrect username or password.';
      }
      return error.message;
    }
    return 'Something went wrong. Please try again.';
  }

  @override
  Widget build(BuildContext context) {
    final authState = ref.watch(authRepositoryProvider);
    final needsNewPassword = authState is AuthNeedsNewPassword;

    return Scaffold(
      backgroundColor: AppColors.bg,
      body: Stack(
        children: [
          const PageGlow(),
          Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 400),
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Center(child: BrandMark(size: 48)),
                    const SizedBox(height: 32),
                    Text(
                      needsNewPassword ? 'Set a new password' : 'Sign in',
                      style: AppTextStyles.pageH1(),
                      textAlign: TextAlign.center,
                    ),
                    const SizedBox(height: 24),
                    if (_errorMessage != null) ...[
                      Text(_errorMessage!, style: TextStyle(color: AppColors.neg)),
                      const SizedBox(height: 16),
                    ],
                    if (needsNewPassword) ..._newPasswordFields() else ..._loginFields(),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  List<Widget> _loginFields() {
    return [
      TextField(
        controller: _usernameController,
        decoration: const InputDecoration(labelText: 'Username'),
        textInputAction: TextInputAction.next,
      ),
      const SizedBox(height: 16),
      TextField(
        controller: _passwordController,
        decoration: const InputDecoration(labelText: 'Password'),
        obscureText: true,
        onSubmitted: (_) => _submitLogin(),
      ),
      const SizedBox(height: 24),
      ElevatedButton(
        onPressed: _isSubmitting ? null : _submitLogin,
        child: _isSubmitting ? const _ButtonSpinner() : const Text('Sign in'),
      ),
    ];
  }

  List<Widget> _newPasswordFields() {
    return [
      TextField(
        controller: _newPasswordController,
        decoration: const InputDecoration(labelText: 'New password'),
        obscureText: true,
        onSubmitted: (_) => _submitNewPassword(),
      ),
      const SizedBox(height: 24),
      ElevatedButton(
        onPressed: _isSubmitting ? null : _submitNewPassword,
        child: _isSubmitting ? const _ButtonSpinner() : const Text('Set password'),
      ),
    ];
  }

  void _submitLogin() {
    _run(() => ref.read(authRepositoryProvider.notifier).login(
          username: _usernameController.text.trim(),
          password: _passwordController.text,
        ));
  }

  void _submitNewPassword() {
    _run(() => ref.read(authRepositoryProvider.notifier).respondToNewPassword(_newPasswordController.text));
  }
}

class _ButtonSpinner extends StatelessWidget {
  const _ButtonSpinner();

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 18,
      height: 18,
      child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.bg),
    );
  }
}
