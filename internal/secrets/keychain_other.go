//go:build !darwin

package secrets

// IsKeychainLockedError returns false on non-macOS platforms.
func IsKeychainLockedError(_ string) bool {
	return false
}
