package version

import "testing"

func TestAssetName(t *testing.T) {
	name := AssetName()
	if name == "" {
		t.Fatal("empty asset name")
	}
}
