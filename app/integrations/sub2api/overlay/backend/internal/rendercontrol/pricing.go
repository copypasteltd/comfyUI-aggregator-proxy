package rendercontrol

import (
	"context"
	"database/sql"
	"fmt"
	"math/big"
	"regexp"
	"strconv"
	"strings"
)

// Rate is a nonnegative NUMERIC(10,4), distinct from eight-decimal Money.
type Rate int64

const rateScale int64 = 10000

var ratePattern = regexp.MustCompile(`^(0|[1-9][0-9]{0,5})(\.[0-9]{1,4})?$`)

func parseRate(value string) (Rate, error) {
	if !ratePattern.MatchString(value) {
		return 0, ErrInvalid
	}
	parts := strings.SplitN(value, ".", 2)
	whole, _ := strconv.ParseInt(parts[0], 10, 64)
	fraction := int64(0)
	if len(parts) == 2 {
		fraction, _ = strconv.ParseInt(parts[1]+strings.Repeat("0", 4-len(parts[1])), 10, 64)
	}
	return Rate(whole*rateScale + fraction), nil
}

func (r Rate) String() string { return fmt.Sprintf("%d.%04d", int64(r)/rateScale, int64(r)%rateScale) }

func pricedAmount(unit Money, units int64, rate Rate) (Money, error) {
	base, err := unit.multiply(units)
	if err != nil || rate < 0 || rate > 9_999_999_999 {
		return 0, ErrInvalid
	}
	// Round once after multiplying the complete measured amount. Potential
	// products can exceed int64 and must be rejected without wrapping first.
	value := new(big.Int).Mul(big.NewInt(int64(base)), big.NewInt(int64(rate)))
	value.Add(value, big.NewInt(rateScale/2)).Quo(value, big.NewInt(rateScale))
	if !value.IsInt64() || value.Int64() > int64(maxMoney) {
		return 0, ErrInvalid
	}
	return Money(value.Int64()), nil
}

func loadPricing(ctx context.Context, tx *sql.Tx, userID, keyID int64, task string) (group int64, rate Rate, source string, billing int, err error) {
	var raw, mode string
	err = tx.QueryRowContext(ctx, `SELECT g.id,g.subscription_type,
		(CASE WHEN $3 IN ('text_to_video','references_to_video') AND g.video_rate_independent THEN g.video_rate_multiplier
		 WHEN $3 IN ('text_to_image','image_to_image','references_to_image') AND g.image_rate_independent THEN g.image_rate_multiplier
		 ELSE COALESCE(o.rate_multiplier,g.rate_multiplier) END)::text,
		CASE WHEN $3 IN ('text_to_video','references_to_video') AND g.video_rate_independent THEN 'video'
		 WHEN $3 IN ('text_to_image','image_to_image','references_to_image') AND g.image_rate_independent THEN 'image'
		 WHEN o.rate_multiplier IS NOT NULL THEN 'user' ELSE 'group' END
		FROM api_keys k JOIN groups g ON g.id=k.group_id
		LEFT JOIN user_group_rate_multipliers o ON o.group_id=g.id AND o.user_id=$1 WHERE k.id=$2`, userID, keyID, task).Scan(&group, &mode, &raw, &source)
	if err != nil {
		err = ErrStorage
		return
	}
	if rate, err = parseRate(raw); err != nil {
		err = ErrUnsupported
		return
	}
	if mode == "subscription" {
		billing = 1
	} else if mode != "standard" {
		err = ErrUnsupported
	}
	return
}
