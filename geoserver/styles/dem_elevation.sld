<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor version="1.0.0"
  xmlns="http://www.opengis.net/sld"
  xmlns:ogc="http://www.opengis.net/ogc"
  xmlns:xlink="http://www.w3.org/1999/xlink"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://www.opengis.net/sld http://schemas.opengis.net/sld/1.0.0/StyledLayerDescriptor.xsd">
  <NamedLayer>
    <Name>dem_elevation</Name>
    <UserStyle>
      <Title>Glasgow Phase V DTM elevation</Title>
      <Abstract>
        Hypsometric colour ramp for the four-tile Glasgow 10 km 50 cm DTM mosaic. The ramp uses the
        observed raster range of -1.495 m to 91.13 m; NoData (-9999) is transparent.
      </Abstract>
      <FeatureTypeStyle>
        <Rule>
          <RasterSymbolizer>
            <Opacity>1.0</Opacity>
            <ColorMap type="ramp" extended="true">
              <ColorMapEntry color="#000000" quantity="-9999" opacity="0.0" label="No data"/>
              <ColorMapEntry color="#08306b" quantity="-1.495" label="-1.5 m"/>
              <ColorMapEntry color="#2b8cbe" quantity="0" label="0 m"/>
              <ColorMapEntry color="#7bccc4" quantity="5" label="5 m"/>
              <ColorMapEntry color="#bae4bc" quantity="10" label="10 m"/>
              <ColorMapEntry color="#f7fcb9" quantity="20" label="20 m"/>
              <ColorMapEntry color="#fec44f" quantity="35" label="35 m"/>
              <ColorMapEntry color="#d95f0e" quantity="50" label="50 m"/>
              <ColorMapEntry color="#8c2d04" quantity="70" label="70 m"/>
              <ColorMapEntry color="#f7f7f7" quantity="91.13" label="91.1 m"/>
            </ColorMap>
          </RasterSymbolizer>
        </Rule>
      </FeatureTypeStyle>
    </UserStyle>
  </NamedLayer>
</StyledLayerDescriptor>
