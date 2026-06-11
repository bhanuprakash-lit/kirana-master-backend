# Telemetry Specification for Kirana AI

To enable security monitoring and active session tracking in the Admin Panel, the Flutter mobile application MUST send device telemetry headers during authentication.

## Target Endpoints
The following endpoints will capture telemetry data from headers:
1. `POST /kirana/auth/login` (Password Login)
2. `POST /kirana/auth/phone-login` (OTP Login)
3. `POST /pos/token` (POS System Login)

## Required Headers

| Header | Description | Example |
| :--- | :--- | :--- |
| `X-Device-Brand` | The manufacturer of the device. | `Samsung`, `Apple` |
| `X-Device-Model` | The specific model of the device. | `Galaxy S23`, `iPhone 15 Pro` |
| `X-OS-Name` | The operating system name. | `Android`, `iOS` |
| `X-OS-Version` | The OS version string. | `14.0.1`, `17.2` |

## Data Persistence
The backend will automatically extract these headers along with the client's **IP Address** and store them in the `kirana_oltp.user_sessions` table. This allows admins to monitor active sessions and identify unauthorized access from unusual devices or locations.

## Implementation Guide (Flutter)
Use the `device_info_plus` package in Flutter to populate these headers in your `ApiClient` or `AuthService`.

```dart
// Example Header Construction
final headers = {
  'X-Device-Brand': deviceInfo.manufacturer,
  'X-Device-Model': deviceInfo.model,
  'X-OS-Name': Platform.isAndroid ? 'Android' : 'iOS',
  'X-OS-Version': deviceInfo.version.release,
};
```
