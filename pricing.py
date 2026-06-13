def calculate_item_price(
    base_rate: float, 
    qty: float, 
    labor_cost: float = 0.0, 
    margin_percent: float = 0.0, 
    gst_percent: float = 0.0,
    pricing_type: str = "piece",
    length: float = 1.0,
    breadth: float = 1.0,
    height: float = 1.0
) -> dict:
    """
    Computes pricing breakdown for a single item.
    Formula:
    1. Base cost = base_rate + labor_cost
    2. Cost with profit margin = Base cost * (1 + margin_percent/100)
    3. Final unit price (with GST) = Cost with profit margin * (1 + gst_percent/100)
    4. Total amount = Final unit price * qty * area (if pricing_type is 'sq_ft')
    """
    # 1. Base unit cost combining material rate and labor rate
    unit_cost_before_margin = base_rate + labor_cost
    
    # 2. Add profit margin
    margin_multiplier = 1.0 + (margin_percent / 100.0) if margin_percent > 0.0 else 1.0
    unit_price_after_margin = unit_cost_before_margin * margin_multiplier
    
    # 3. Add GST
    gst_multiplier = 1.0 + (gst_percent / 100.0) if gst_percent > 0.0 else 1.0
    final_unit_price = unit_price_after_margin * gst_multiplier
    
    # Calculate multiplier based on pricing type
    if pricing_type == "sq_ft":
        l_val = length if length not in (None, 0.0) else 1.0
        h_val = height if height not in (None, 0.0) else 0.0
        b_val = breadth if breadth not in (None, 0.0) else 0.0
        
        second_dim = h_val if h_val > 0.0 else (b_val if b_val > 0.0 else 1.0)
        area = l_val * second_dim
        multiplier = qty * area
    elif pricing_type == "running_ft":
        l_val = length if length not in (None, 0.0) else 1.0
        multiplier = qty * l_val
    else:
        multiplier = qty

    # GST-inclusive total amount
    total_amount = final_unit_price * multiplier
    
    # Pre-tax subtotal (without GST)
    subtotal_item_total = unit_price_after_margin * multiplier
    
    # GST contribution (item-wise)
    gst_amount = total_amount - subtotal_item_total
    
    return {
        "unit_cost_before_margin": round(unit_cost_before_margin, 2),
        "unit_price_after_margin": round(unit_price_after_margin, 2),
        "final_unit_price": round(final_unit_price, 2),
        "total_amount": round(total_amount, 2),
        "gst_amount": round(gst_amount, 2),
        "subtotal": round(total_amount, 2)
    }
